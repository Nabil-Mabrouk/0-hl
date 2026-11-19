"""M1 acceptance report — read-only (spec 12-tests/02-recette-m1.md).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.recette [--bot NAME]
[--hours 24] [--json]. Exit code 0 when every blocking criterion is OK, 1 otherwise.
Writes nothing: the reconciliation check runs in a transaction that is rolled back.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents.equity import reconcile
from app.domain.trading.enums import (
    BotStatus,
    CommandStatus,
    IntentPurpose,
    PositionStatus,
    SessionStatus,
)
from app.domain.trading.events import verify_checksum
from app.domain.trading.models import (
    Bot,
    BotSession,
    Command,
    DomainEvent,
    Instrument,
    LedgerEntry,
    Order,
    OrderIntent,
    Position,
    TradeMinute,
)
from app.domain.trading.types import to_jsonable, utcnow

ZERO = Decimal(0)
TRACE_REQUIRED = ("CANDIDATE_DETECTED", "RISK_PASSED", "POSITION_SIZED", "ORDER_FILLED",
                  "POSITION_OPENED", "POSITION_CLOSED")


@dataclass
class Check:
    id: str
    title: str
    status: str            # OK | KO | INFO
    blocking: bool
    details: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Report:
    bot: str
    generated_at: datetime
    window_hours: float
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.status == "OK" for c in self.checks if c.blocking)

    def as_json(self) -> dict[str, Any]:
        return to_jsonable({
            "bot": self.bot, "generated_at": self.generated_at, "window_hours": str(self.window_hours),
            "passed": self.passed,
            "checks": [{"id": c.id, "title": c.title, "status": c.status, "blocking": c.blocking,
                        "details": c.details, "data": c.data} for c in self.checks],
        })

    def as_text(self) -> str:
        lines = [f"0-hl — recette M1 — bot {self.bot} — fenêtre {self.window_hours} h — "
                 f"{self.generated_at.isoformat()}", ""]
        for c in self.checks:
            tag = c.status + (" (bloquant)" if c.blocking and c.status != "OK" else "")
            lines.append(f"[{tag:>4}] {c.id} {c.title}")
            lines += [f"         {d}" for d in c.details]
        lines += ["", f"GATE M1 : {'FRANCHI' if self.passed else 'NON FRANCHI'}"]
        return "\n".join(lines)


def _ok(cid, title, problems: list[str], blocking=True, **data) -> Check:
    return Check(cid, title, "KO" if problems else "OK", blocking, problems[:20], data)


async def check_ledger(db: AsyncSession, bot: Bot) -> Check:
    sums: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    rows = await db.execute(select(LedgerEntry.transaction_id, LedgerEntry.asset, LedgerEntry.amount)
                            .where(LedgerEntry.bot_id == bot.id))
    n = 0
    for tx, asset, amount in rows:
        sums[(tx, asset)] += amount
        n += 1
    bad = [f"transaction {tx} {asset}: somme {total}" for (tx, asset), total in sums.items() if total != ZERO]
    return _ok("R-01", "ledger équilibré", bad, entries=n, transactions=len({k[0] for k in sums}))


async def check_reconciliation(sessions: async_sessionmaker[AsyncSession], bot: Bot) -> Check:
    async with sessions() as db:
        try:
            result = await reconcile(db, bot.id)
        finally:
            await db.rollback()   # read-only: orphan-intent expiry is not persisted
    details = list(result.issues)
    if result.expired_intents:
        details.append(f"{result.expired_intents} intention(s) orpheline(s) (seraient expirées)")
    return _ok("R-02", "réconciliation à l'instant", details)


async def check_idempotence(db: AsyncSession, bot: Bot) -> Check:
    problems: list[str] = []
    dup_orders = await db.execute(
        select(Order.intent_id, func.count()).join(OrderIntent, OrderIntent.id == Order.intent_id)
        .where(OrderIntent.bot_id == bot.id, OrderIntent.purpose == IntentPurpose.ENTRY)
        .group_by(Order.intent_id).having(func.count() > 1))
    problems += [f"intention {i}: {n} ordres" for i, n in dup_orders]
    dup_intents = await db.execute(
        select(OrderIntent.risk_approval_id, func.count()).where(
            OrderIntent.bot_id == bot.id, OrderIntent.risk_approval_id.is_not(None))
        .group_by(OrderIntent.risk_approval_id).having(func.count() > 1))
    problems += [f"autorisation {a}: {n} intentions" for a, n in dup_intents]
    return _ok("R-03", "un seul ordre par intention, une seule intention par autorisation", problems)


async def check_traces(db: AsyncSession, bot: Bot) -> Check:
    closed = (await db.scalars(select(Position).where(
        Position.bot_id == bot.id, Position.status == PositionStatus.CLOSED))).all()
    problems: list[str] = []
    for p in closed:
        corr = await db.scalar(select(OrderIntent.candidate_id).where(
            OrderIntent.position_id == p.id, OrderIntent.purpose == IntentPurpose.ENTRY).limit(1))
        if corr is None:
            problems.append(f"position {p.id}: aucune intention d'entrée liée à un candidat")
            continue
        types = set((await db.scalars(select(DomainEvent.event_type).where(
            DomainEvent.correlation_id == corr))).all())
        missing = [t for t in TRACE_REQUIRED if t not in types]
        if missing:
            problems.append(f"position {p.id} ({p.instrument_id}): manque {', '.join(missing)}")
    return _ok("R-04", "chaque trade clos est reconstructible", problems, closed_positions=len(closed))


async def check_journal(db: AsyncSession) -> Check:
    problems: list[str] = []
    last: dict[tuple[str, str], int] = {}
    n = 0
    events = (await db.scalars(select(DomainEvent).order_by(
        DomainEvent.aggregate_type, DomainEvent.aggregate_id, DomainEvent.aggregate_sequence))).all()
    for e in events:
        n += 1
        if not verify_checksum(e):
            problems.append(f"checksum invalide : événement {e.position} ({e.event_type})")
        key = (e.aggregate_type, e.aggregate_id)
        expected = last.get(key, 0) + 1
        if e.aggregate_sequence != expected:
            problems.append(f"séquence {key}: attendu {expected}, trouvé {e.aggregate_sequence}")
        last[key] = e.aggregate_sequence
    return _ok("R-05", "journal intègre (checksums, séquences)", problems, events=n)


async def check_health_events(db: AsyncSession, bot: Bot, since: datetime) -> Check:
    rows = (await db.scalars(select(DomainEvent).where(
        DomainEvent.bot_id == bot.id, DomainEvent.occurred_at >= since,
        DomainEvent.event_type.in_(["RECONCILIATION_FAILED", "BOT_STATUS_CHANGED"])))).all()
    problems = []
    for e in rows:
        if e.event_type == "RECONCILIATION_FAILED":
            problems.append(f"{e.occurred_at.isoformat()} RECONCILIATION_FAILED {e.payload.get('issues')}")
        elif e.payload.get("to") in (BotStatus.DEGRADED.value, BotStatus.FAILED.value):
            problems.append(f"{e.occurred_at.isoformat()} bot → {e.payload.get('to')} ({e.payload.get('reason')})")
    return _ok("R-06", "aucune réconciliation en échec ni dégradation", problems)


async def check_commands(db: AsyncSession, bot: Bot, now: datetime) -> Check:
    stuck = (await db.scalars(select(Command).where(
        Command.bot_id == bot.id, Command.status == CommandStatus.PENDING,
        Command.requested_at < now - timedelta(seconds=60)))).all()
    return _ok("R-07", "aucune commande bloquée", [f"{c.command.value} {c.id} depuis {c.requested_at.isoformat()}"
                                                   for c in stuck])


async def check_session(db: AsyncSession, bot: Bot, now: datetime, hours: float) -> Check:
    s = await db.scalar(select(BotSession).where(BotSession.bot_id == bot.id, BotSession.ended_at.is_(None))
                        .order_by(BotSession.started_at.desc()).limit(1))
    if s is None:
        return Check("R-08", f"session continue ≥ {hours} h", "KO", True, ["aucune session ouverte"])
    age = now - s.started_at
    problems = []
    if s.status is not SessionStatus.RUNNING or bot.status is not BotStatus.RUNNING:
        problems.append(f"bot {bot.status.value}, session {s.status.value}")
    if age < timedelta(hours=hours):
        problems.append(f"session ouverte depuis {age} seulement")
    c = _ok("R-08", f"session continue ≥ {hours} h", problems, session_started_at=s.started_at,
            session_age_hours=f"{age.total_seconds() / 3600:.2f}")
    if not problems:
        c.details = [f"session ouverte depuis {age}"]
    return c


async def info_continuity(db: AsyncSession, since: datetime) -> Check:
    details: list[str] = []
    data: dict[str, Any] = {}
    try:
        from app.modules.worker.models import WorkerRun
        async with db.begin_nested():  # a missing chassis table must not abort the read transaction
            starts = await db.scalar(select(func.count()).select_from(WorkerRun).where(WorkerRun.started_at >= since))
        data["worker_starts"] = starts
        details.append(f"démarrages du worker sur la fenêtre : {starts}")
    except Exception:  # noqa: BLE001 — chassis table absent (tests/dev)
        details.append("démarrages du worker : n/d")
    counts = Counter((await db.scalars(select(DomainEvent.event_type).where(
        DomainEvent.occurred_at >= since,
        DomainEvent.event_type.in_(["PROVIDER_DEGRADED", "PROVIDER_RECOVERED", "DATA_STALE"])))).all())
    data |= dict(counts)
    details.append(f"connexions perdues : {counts.get('PROVIDER_DEGRADED', 0)} · "
                   f"paires passées STALE : {counts.get('DATA_STALE', 0)}")
    return Check("R-09", "continuité du worker et du flux", "INFO", False, details, data)


async def info_coverage(db: AsyncSession, since: datetime, now: datetime) -> Check:
    iids = (await db.scalars(select(Instrument.id).where(Instrument.in_universe))).all()
    expected = max(int((now - since).total_seconds() // 60) - 1, 1)
    worst: list[tuple[float, str]] = []
    for iid in iids:
        n = await db.scalar(select(func.count()).select_from(TradeMinute).where(
            TradeMinute.instrument_id == iid, TradeMinute.source == "live", TradeMinute.minute_start >= since))
        worst.append((min(1.0, (n or 0) / expected), iid))
    worst.sort()
    avg = sum(c for c, _ in worst) / len(worst) if worst else 0.0
    details = [f"couverture moyenne des minutes live : {avg:.1%} sur {len(iids)} paires"]
    details += [f"  {iid}: {c:.1%}" for c, iid in worst[:3]]
    return Check("R-10", "couverture des données de marché", "INFO", False, details,
                 {"average_live_coverage": f"{avg:.4f}"})


async def info_activity(db: AsyncSession, bot: Bot, since: datetime) -> Check:
    types = Counter((await db.scalars(select(DomainEvent.event_type).where(
        DomainEvent.bot_id == bot.id, DomainEvent.occurred_at >= since,
        DomainEvent.event_type.in_(["CANDIDATE_DETECTED", "RISK_REJECTED", "POSITION_OPENED"])))).all())
    closed = (await db.scalars(select(Position).where(
        Position.bot_id == bot.id, Position.status == PositionStatus.CLOSED, Position.closed_at >= since))).all()
    pnl = sum((p.realized_pnl_net or ZERO for p in closed), ZERO)
    fees = sum((p.fees_paid for p in closed), ZERO)
    wins = sum(1 for p in closed if (p.realized_pnl_net or ZERO) > 0)
    losses = sum(1 for p in closed if (p.realized_pnl_net or ZERO) < 0)
    rs = [p.realized_pnl_net / p.r0_usd for p in closed if p.realized_pnl_net is not None and p.r0_usd]
    reasons = Counter(p.exit_reason for p in closed)
    details = [
        f"candidats {types.get('CANDIDATE_DETECTED', 0)} · rejets {types.get('RISK_REJECTED', 0)} · "
        f"entrées {types.get('POSITION_OPENED', 0)} · sorties {len(closed)} ({dict(reasons)})",
        f"wins {wins} / losses {losses} · PnL net {pnl:.2f} USD · frais {fees:.2f} USD"
        + (f" · R moyen {sum(rs) / len(rs):.2f}" if rs else ""),
    ]
    return Check("R-11", "activité de trading", "INFO", False, details,
                 {"pnl_net": pnl, "fees": fees, "wins": wins, "losses": losses, "exits": dict(reasons)})


async def info_strategy(db: AsyncSession, bot: Bot, since: datetime) -> Check:
    fails: Counter = Counter()
    unknown: Counter = Counter()
    evaluated = cycles = 0
    for payload in (await db.scalars(select(DomainEvent.payload).where(
            DomainEvent.bot_id == bot.id, DomainEvent.event_type == "SCAN_CYCLE_COMPLETED",
            DomainEvent.occurred_at >= since))).all():
        cycles += 1
        evaluated += int(payload.get("evaluated", 0))
        fails.update(payload.get("fails", {}))
        unknown.update(payload.get("unknown_reasons", {}))
    details = [f"{cycles} cycles de scan, {evaluated} évaluations"]
    details += [f"  échec {k}: {v} ({v / evaluated:.1%})" for k, v in fails.most_common()] if evaluated else []
    details += [f"  inconnu {k}: {v}" for k, v in unknown.most_common(5)]
    return Check("R-12", "diagnostic STRAT-01 (conditions bloquantes)", "INFO", False, details,
                 {"cycles": cycles, "evaluated": evaluated, "fails": dict(fails), "unknown": dict(unknown)})


async def build_report(sessions: async_sessionmaker[AsyncSession], *, bot_name: str | None = None,
                       hours: float = 24.0, now: datetime | None = None) -> Report:
    now = now or utcnow()
    since = now - timedelta(hours=hours)
    async with sessions() as db:
        stmt = select(Bot).where(Bot.name == bot_name) if bot_name else select(Bot).order_by(Bot.created_at)
        bot = await db.scalar(stmt.limit(1))
        if bot is None:
            raise SystemExit(f"bot introuvable : {bot_name or '(aucun bot)'}")
        checks = [
            await check_ledger(db, bot),
            await check_reconciliation(sessions, bot),
            await check_idempotence(db, bot),
            await check_traces(db, bot),
            await check_journal(db),
            await check_health_events(db, bot, since),
            await check_commands(db, bot, now),
            await check_session(db, bot, now, hours),
            await info_continuity(db, since),
            await info_coverage(db, since, now),
            await info_activity(db, bot, since),
            await info_strategy(db, bot, since),
        ]
    return Report(bot=bot.name, generated_at=now, window_hours=hours, checks=checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — recette du jalon M1 (lecture seule)")
    parser.add_argument("--bot", default=None)
    parser.add_argument("--hours", type=float, default=24.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal, engine

    async def _run() -> Report:
        try:
            return await build_report(SessionLocal, bot_name=args.bot, hours=args.hours)
        finally:
            await engine.dispose()

    report = asyncio.run(_run())
    if hasattr(sys.stdout, "reconfigure"):  # report uses ≥ and —: never crash on a non-UTF-8 console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False) if args.json else report.as_text())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
