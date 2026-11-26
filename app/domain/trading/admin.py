"""Admin command for versions and bot binding — M2.3 (spec 05-strategies/04 « Commande d'administration »).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.admin --as EMAIL <kind> <verb> …

  strategy list | show ID@VER | diff ID@A ID@B | validate --file F | create --file F
           duplicate ID@VER --version V | edit ID@VER --file F | delete ID@VER
           publish ID@VER --reason R [--backtest-ref SHA] | deprecate ID@VER --reason R
  policy   (same verbs) | confirm ID@VER
  bot      list | bind NAME --strategy ID@VER --policy ID@VER --reason R
           create NAME --strategy ID@VER --policy ID@VER [--capital 10000] --reason R
           rename NAME NEW_NAME --reason R | delete NAME --reason R   (ADR-032)

Calls the same domain functions as the API (same validation, audits and errors); the user
named by --as must hold the role the API would require. --dry-run validates and shows the
result, then rolls back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.models import User
from app.domain.trading import lifecycle as lc
from app.domain.trading.api.snapshot import bot_json
from app.domain.trading.api.versions import policy_json, strategy_json
from app.domain.trading.audit import user_actor
from app.domain.trading.enums import TradingRole
from app.domain.trading.errors import DomainError, NotFound, PermissionDenied, ValidationFailed
from app.domain.trading.models import Bot, RiskPolicyVersion, StrategyVersion
from app.domain.trading.rbac import active_roles
from app.domain.trading.types import to_jsonable
from app.domain.trading.versioning import create_policy_version, create_strategy_version

A, R, O = TradingRole.ANALYST, TradingRole.RISK_OFFICER, TradingRole.OPERATOR
READ = frozenset(TradingRole)
# verb -> roles that must ALL be held (API: Analyst / Risk Officer / Operator + Risk Officer)
NEEDS: dict[tuple[str, str], tuple[TradingRole, ...]] = {
    ("strategy", "create"): (A,), ("strategy", "duplicate"): (A,), ("strategy", "edit"): (A,),
    ("strategy", "delete"): (A,), ("strategy", "publish"): (R,), ("strategy", "deprecate"): (R,),
    **{("policy", v): (R,) for v in ("create", "duplicate", "edit", "delete", "publish", "confirm",
                                     "deprecate")},
    ("bot", "bind"): (O, R), ("bot", "create"): (O, R), ("bot", "rename"): (O,), ("bot", "delete"): (O, R),
}


def _ref(ref: str) -> tuple[str, str]:
    ident, sep, version = ref.partition("@")
    if not sep or not ident or not version:
        raise ValidationFailed(f"« {ref} » : forme attendue ID@VERSION")
    return ident, version


async def _strategy(db: AsyncSession, ref: str) -> StrategyVersion:
    ident, version = _ref(ref)
    v = await db.scalar(select(StrategyVersion).where(StrategyVersion.strategy_id == ident,
                                                      StrategyVersion.version == version))
    if v is None:
        raise NotFound(f"stratégie {ref} introuvable")
    return v


async def _policy(db: AsyncSession, ref: str) -> RiskPolicyVersion:
    ident, version = _ref(ref)
    v = await db.scalar(select(RiskPolicyVersion).where(RiskPolicyVersion.policy_id == ident,
                                                        RiskPolicyVersion.version == version))
    if v is None:
        raise NotFound(f"policy {ref} introuvable")
    return v


def _file(path: str | None) -> dict[str, Any]:
    if not path:
        raise ValidationFailed("--file est requis")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValidationFailed(f"{path} : lecture impossible ({exc})") from exc


async def run(db: AsyncSession, args: argparse.Namespace) -> Any:
    user = await db.scalar(select(User).where(User.email == args.as_email))
    if user is None:
        raise NotFound(f"utilisateur {args.as_email} introuvable")
    held = await active_roles(db, user.id)
    needed = NEEDS.get((args.kind, args.verb))
    if not held & READ or (needed and not all(r in held for r in needed)):
        raise PermissionDenied(f"{args.kind} {args.verb} exige {', '.join(r.value for r in needed or ())}"
                               if needed else "aucun rôle trading")
    actor = user_actor(user.id)
    kind, verb = args.kind, args.verb
    if kind == "bot":
        if verb == "list":
            return [bot_json(b) for b in (await db.scalars(
                select(Bot).where(Bot.deleted_at.is_(None)).order_by(Bot.name))).all()]
        if verb == "create":
            from decimal import Decimal, InvalidOperation
            try:
                capital = Decimal(args.capital or lc.DEFAULT_CAPITAL)
            except InvalidOperation as exc:
                raise ValidationFailed("--capital : montant décimal attendu") from exc
            bot = await lc.create_paper_bot(db, name=args.target or "", strategy_version=await _strategy(db, args.strategy),
                                            risk_policy_version=await _policy(db, args.policy), capital=capital,
                                            actor=actor, reason=args.reason or "")
            return bot_json(bot) | {"capital_usd": capital}
        bot = await db.scalar(select(Bot).where(Bot.name == args.target, Bot.deleted_at.is_(None)))
        if bot is None:
            raise NotFound(f"bot {args.target} introuvable")
        if verb == "rename":
            return bot_json(await lc.rename_bot(db, bot, name=args.other or "", actor=actor,
                                                reason=args.reason or ""))
        if verb == "delete":
            return bot_json(await lc.delete_bot(db, bot, actor=actor, reason=args.reason or ""))
        if verb != "bind":
            raise ValidationFailed(f"verbe inconnu : bot {verb}")
        bot = await lc.bind_bot(db, bot, strategy_version=await _strategy(db, args.strategy),
                                risk_policy_version=await _policy(db, args.policy), actor=actor,
                                reason=args.reason or "")
        return bot_json(bot)

    is_s = kind == "strategy"
    model = StrategyVersion if is_s else RiskPolicyVersion
    show = strategy_json if is_s else policy_json
    get = _strategy if is_s else _policy
    if verb == "list":
        bound = await lc.bots_bound_to(db)
        order = (StrategyVersion.strategy_id if is_s else RiskPolicyVersion.policy_id, model.created_at)
        return [show(v, bound) for v in (await db.scalars(select(model).order_by(*order))).all()]
    if verb == "show":
        return show(await get(db, args.target), await lc.bots_bound_to(db))
    if verb == "diff":
        va, vb = await get(db, args.target), await get(db, args.other)
        return lc.diff_configs(va.config, vb.config) if is_s else lc.policy_diff(va.config, vb.config)
    if verb == "validate":
        cfg = _file(args.file)
        errors = await lc.validate_strategy(db, cfg) if is_s else await lc.validate_policy(cfg)
        return {"valid": not errors, "errors": errors}
    if verb == "create":
        cfg = _file(args.file)
        v = (await create_strategy_version(db, config={**cfg, "status": "DRAFT"},
                                           name=str(cfg.get("strategy_id")), actor=actor) if is_s
             else await create_policy_version(db, config=cfg, name=str(cfg.get("policy_id")), actor=actor))
        return show(v)
    if verb == "duplicate":
        if not args.version:
            raise ValidationFailed("--version est requis")
        dup = lc.duplicate_strategy_version if is_s else lc.duplicate_policy_version
        return show(await dup(db, await get(db, args.target), version=args.version, actor=actor))
    if verb == "edit":
        edit = lc.edit_strategy_draft if is_s else lc.edit_policy_draft
        return show(await edit(db, await get(db, args.target), config=_file(args.file), actor=actor,
                               if_match=None))
    if verb == "delete":
        v = await get(db, args.target)
        await (lc.delete_strategy_draft if is_s else lc.delete_policy_draft)(db, v, actor=actor)
        return {"deleted": args.target}
    if verb == "publish":
        v = await get(db, args.target)
        if is_s:
            return show(await lc.publish_strategy(db, v, actor=actor, reason=args.reason or "",
                                                  backtest_ref=args.backtest_ref))
        out = await lc.request_policy_publication(db, v, actor=actor, reason=args.reason or "")
        return show(out.version) | {"outcome": out.status, "loosened": out.loosened,
                                    "confirm_after": out.confirm_after}
    if verb == "confirm" and not is_s:
        return show(await lc.confirm_policy_publication(db, await get(db, args.target), actor=actor))
    if verb == "deprecate":
        dep = lc.deprecate_strategy if is_s else lc.deprecate_policy
        return show(await dep(db, await get(db, args.target), actor=actor, reason=args.reason or ""))
    raise ValidationFailed(f"verbe inconnu : {kind} {verb}")


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="0-hl — versions et rattachement des bots (M2.3)")
    ap.add_argument("--as", dest="as_email", required=True, help="e-mail d'un utilisateur portant le rôle")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("kind", choices=("strategy", "policy", "bot"))
    ap.add_argument("verb")
    ap.add_argument("target", nargs="?")
    ap.add_argument("other", nargs="?")
    ap.add_argument("--file")
    ap.add_argument("--version")
    ap.add_argument("--reason")
    ap.add_argument("--backtest-ref")
    ap.add_argument("--strategy")
    ap.add_argument("--policy")
    ap.add_argument("--capital")
    return ap


async def execute(sessions: async_sessionmaker[AsyncSession], argv: list[str]) -> tuple[int, Any]:
    args = parser().parse_args(argv)
    async with sessions() as db:
        try:
            result = await run(db, args)
        except DomainError as exc:
            await db.rollback()
            return 1, {"code": exc.code, "message": exc.message, **({"details": exc.details} if exc.details else {})}
        if args.dry_run:
            await db.rollback()
            return 0, {"dry_run": True, "result": result}
        await db.commit()
        return 0, result


def main(argv: list[str] | None = None) -> int:
    from app.core.database import SessionLocal, engine

    async def _run() -> tuple[int, Any]:
        try:
            return await execute(SessionLocal, argv if argv is not None else sys.argv[1:])
        finally:
            await engine.dispose()

    code, out = asyncio.run(_run())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(to_jsonable(out), indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
