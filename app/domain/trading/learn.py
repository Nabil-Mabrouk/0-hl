"""Public strategy data of the Learn section — spec 03-ecrans/UI-17 « API publique » (ADR-029).

Read-only, percentages only: never an absolute amount (capital, P&L or size in USD), internal id
or user data (ADR-028). Statistics come from the same functions as UI-12 (analytics.py).
"""

from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.analytics import compute_stats, performance_curve, trade_rows
from app.domain.trading.enums import BotStatus, StrategyVersionStatus
from app.domain.trading.lifecycle import initial_capital
from app.domain.trading.models import (
    AuditEvent, Bot, BotSession, EquitySnapshot, RiskPolicyVersion, Strategy, StrategyVersion,
)

CACHE_SECONDS = 60.0
_cache: dict[str, tuple[float, Any]] = {}
PUBLISHED = (StrategyVersionStatus.PUBLISHED, StrategyVersionStatus.DEPRECATED)
_INTERNAL = {"risk_policy_id", "status"}


def _q2(x: Decimal | None) -> str | None:
    return None if x is None else format(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


async def _published(db: AsyncSession, strategy_id: str) -> list[StrategyVersion]:
    return list((await db.scalars(select(StrategyVersion).where(
        StrategyVersion.strategy_id == strategy_id, StrategyVersion.status.in_(PUBLISHED))
        .order_by(StrategyVersion.published_at))).all())


async def _bot_for(db: AsyncSession, strategy_id: str) -> tuple[Bot, StrategyVersion] | None:
    """The non-stopped bot running the strategy, else the most recently created one."""
    rows = (await db.execute(select(Bot, StrategyVersion).join(
        StrategyVersion, StrategyVersion.id == Bot.strategy_version_id).where(
        StrategyVersion.strategy_id == strategy_id, Bot.deleted_at.is_(None))
        .order_by(Bot.created_at.desc()))).all()
    for bot, sv in rows:
        if bot.status is not BotStatus.STOPPED:
            return bot, sv
    return (rows[0][0], rows[0][1]) if rows else None


async def _since(db: AsyncSession, bot: Bot) -> Any:
    return await db.scalar(select(BotSession.started_at).where(BotSession.bot_id == bot.id)
                           .order_by(BotSession.started_at).limit(1))


async def _net_pct(db: AsyncSession, bot: Bot) -> str | None:
    equity = await db.scalar(select(EquitySnapshot.equity).where(
        EquitySnapshot.bot_id == bot.id, EquitySnapshot.equity.is_not(None))
        .order_by(EquitySnapshot.at.desc()).limit(1))
    dep = (await initial_capital(db)).get(bot.id)
    return None if equity is None or not dep else _q2((equity - dep) / dep * 100)


async def _head(db: AsyncSession, strat: Strategy, versions: list[StrategyVersion]) -> dict[str, Any]:
    last = versions[-1]
    found = await _bot_for(db, strat.id)
    bot_json = None
    if found is not None:
        bot, _ = found
        rows = await trade_rows(db, bot_id=bot.id)
        bot_json = {"name": bot.name, "status": bot.status.value, "since": await _since(db, bot),
                    "net_pct": await _net_pct(db, bot), "trades": len(rows)}
    return {"id": strat.id, "name": strat.name, "version": last.version, "published_at": last.published_at,
            "bot": bot_json}


async def list_strategies(db: AsyncSession) -> list[dict[str, Any]]:
    out = []
    for strat in (await db.scalars(select(Strategy).order_by(Strategy.id))).all():
        versions = await _published(db, strat.id)
        if versions:
            out.append(await _head(db, strat, versions))
    return out


def public_config(config: dict[str, Any], policy: dict[str, Any] | None) -> dict[str, Any]:
    out = {k: v for k, v in config.items() if k not in _INTERNAL}
    if policy is not None:
        out["risk_per_trade_pct"] = policy.get("max_trade_risk_pct")
        out["max_open_positions"] = policy.get("max_open_positions")
    return out


async def strategy_detail(db: AsyncSession, strategy_id: str, now: Any) -> dict[str, Any] | None:
    strat = await db.get(Strategy, strategy_id)
    versions = await _published(db, strategy_id) if strat else []
    if strat is None or not versions:
        return None
    head = await _head(db, strat, versions)
    found = await _bot_for(db, strategy_id)
    sv = found[1] if found and found[1].status in PUBLISHED else versions[-1]
    policy = None
    if found is not None:
        pv = await db.get(RiskPolicyVersion, found[0].risk_policy_version_id)
        policy = pv.config if pv else None
    stats, trades = None, []
    if found is not None:
        bot = found[0]
        rows = await trade_rows(db, bot_id=bot.id)
        s = compute_stats(rows)
        pcts = [r.net_pct for r in rows if r.net_pct is not None]
        curve = await performance_curve(db, bot.id, max_points=2)
        stats = {"net_pct": head["bot"]["net_pct"], "trades": s["trades"], "wins": s["wins"],
                 "losses": s["losses"], "win_rate_pct": _q2(s["win_rate_pct"]),
                 "best_pct": _q2(max(pcts)) if pcts else None, "worst_pct": _q2(min(pcts)) if pcts else None,
                 "max_drawdown_pct": _q2(curve["max_drawdown_pct"]), "as_of": now}
        trades = [{"symbol": r.position.instrument_id.split(":", 1)[1], "opened_at": r.position.opened_at,
                   "closed_at": r.position.closed_at, "result_pct": _q2(r.net_pct),
                   "exit_reason": r.position.exit_reason} for r in rows[:5]]
    hist = []
    for v in versions:
        reason = await db.scalar(select(AuditEvent.reason).where(
            AuditEvent.action == "STRATEGY_PUBLISHED", AuditEvent.entity_id == str(v.id)).limit(1))
        hist.append({"version": v.version, "published_at": v.published_at, "reason": reason})
    return head | {"config": public_config(sv.config, policy), "config_version": sv.version,
                   "stats": stats, "trades": trades, "versions": hist}


async def cached(key: str, make) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = await make()
    _cache[key] = (time.monotonic(), value)
    return value
