"""M2 acceptance report — read-only (spec 12-tests/03-recette-m2.md « B »).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.recette_m2 [--json]
Exit code 0 when every blocking criterion (R2-01..R2-06) is OK. Writes nothing: the per-bot
reconciliation runs in a transaction that is rolled back (as in the M1 report).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.enums import BotStatus, Environment, PositionStatus
from app.domain.trading.lifecycle import MAX_ACTIVE_BOTS, REVIEW_DELAY
from app.domain.trading.models import (
    AuditEvent,
    BookSnapshot,
    Bot,
    BotSession,
    DomainEvent,
    Instrument,
    LedgerEntry,
    LedgerTransaction,
    OrderIntent,
    Position,
    RiskPolicyVersion,
    StrategyVersion,
    TradeMinute,
)
from app.domain.trading.recette import Check, check_ledger, check_reconciliation
from app.domain.trading.types import sha256_hex, to_jsonable, utcnow

ZERO = Decimal(0)
GATE_DAYS = 28
GOVERNED = ("STRATEGY_PUBLISHED", "STRATEGY_DEPRECATED", "POLICY_UPDATED", "POLICY_DEPRECATED",
            "POLICY_PUBLISH_REQUESTED", "BOT_REBOUND", "BOT_CREATED_BY_OPERATOR")


@dataclass
class ReportM2:
    generated_at: datetime
    checks: list[Check]

    @property
    def passed(self) -> bool:
        return all(c.status == "OK" for c in self.checks if c.blocking)

    def as_json(self) -> dict[str, Any]:
        return to_jsonable({
            "generated_at": self.generated_at, "passed": self.passed,
            "checks": [{"id": c.id, "title": c.title, "status": c.status, "blocking": c.blocking,
                        "details": c.details, "data": c.data} for c in self.checks]})

    def as_text(self) -> str:
        lines = [f"0-hl — recette M2 (12-tests/03, lecture seule) — {self.generated_at.isoformat()}", ""]
        for c in self.checks:
            tag = c.status + (" (bloquant)" if c.blocking and c.status != "OK" else "")
            lines.append(f"[{tag:>4}] {c.id} {c.title}")
            lines += [f"         {d}" for d in c.details]
        lines += ["", f"CRITÈRES BLOQUANTS M2 : {'OK' if self.passed else 'KO'}",
                  "(le gate M2 lui-même suit la procédure C de 12-tests/03 une fois 28 jours d'instantanés disponibles)"]
        return "\n".join(lines)


def _ok(cid: str, title: str, problems: list[str], **data: Any) -> Check:
    return Check(cid, title, "KO" if problems else "OK", True, problems[:20] or ["OK"], data)


async def r2_01_hashes(db: AsyncSession) -> Check:
    bad = []
    for v in (await db.scalars(select(StrategyVersion))).all():
        if sha256_hex(v.config) != v.config_sha256:
            bad.append(f"stratégie {v.strategy_id} v{v.version} : empreinte ≠ hash de la config")
    for v in (await db.scalars(select(RiskPolicyVersion))).all():
        if sha256_hex(v.config) != v.config_sha256:
            bad.append(f"policy {v.policy_id} v{v.version} : empreinte ≠ hash de la config")
    return _ok("R2-01", "empreintes des versions", bad)


async def r2_02_sessions(db: AsyncSession) -> Check:
    rows = await db.execute(select(BotSession, StrategyVersion.config_sha256, RiskPolicyVersion.config_sha256)
                            .join(StrategyVersion, StrategyVersion.id == BotSession.strategy_version_id)
                            .join(RiskPolicyVersion, RiskPolicyVersion.id == BotSession.risk_policy_version_id))
    bad = []
    n = 0
    for s, s_sha, p_sha in rows:
        n += 1
        if s.strategy_sha256 != s_sha or s.risk_policy_sha256 != p_sha:
            bad.append(f"session {s.id} : empreintes différentes de ses versions")
    return _ok("R2-02", "empreintes des sessions", bad, sessions=n)


async def r2_03_bots(db: AsyncSession) -> Check:
    bots = (await db.scalars(select(Bot))).all()
    active = [b for b in bots if b.status is not BotStatus.STOPPED]
    bad = [f"bot {b.name} en {b.environment.value}" for b in bots if b.environment is not Environment.PAPER]
    if len(active) > MAX_ACTIVE_BOTS:
        bad.append(f"{len(active)} bots non arrêtés (plafond {MAX_ACTIVE_BOTS})")
    return _ok("R2-03", f"au plus {MAX_ACTIVE_BOTS} bots actifs, tous PAPER", bad, bots=len(bots), active=len(active))


async def r2_04_books(sessions: async_sessionmaker[AsyncSession], db: AsyncSession) -> Check:
    bad, data = [], {}
    for b in (await db.scalars(select(Bot).order_by(Bot.name))).all():
        ledger = await check_ledger(db, b)
        recon = await check_reconciliation(sessions, b)
        data[b.name] = {"ledger": ledger.status, "reconciliation": recon.status}
        bad += [f"{b.name} : {d}" for c in (ledger, recon) if c.status != "OK" for d in c.details]
    return _ok("R2-04", "ledger et réconciliation de chaque bot", bad, **data)


async def r2_05_isolation(db: AsyncSession) -> Check:
    bad = []
    rows = await db.execute(select(Position.id, Position.bot_id, BotSession.bot_id)
                            .join(BotSession, BotSession.id == Position.session_id))
    bad += [f"position {pid} : session d'un autre bot" for pid, pb, sb in rows if pb != sb]
    rows = await db.execute(select(OrderIntent.id, OrderIntent.bot_id, BotSession.bot_id)
                            .join(BotSession, BotSession.id == OrderIntent.session_id))
    bad += [f"intention {iid} : session d'un autre bot" for iid, ib, sb in rows if ib != sb]
    rows = await db.execute(select(OrderIntent.id, OrderIntent.bot_id, Position.bot_id)
                            .join(Position, Position.id == OrderIntent.position_id))
    bad += [f"intention {iid} : position d'un autre bot" for iid, ib, pb in rows if ib != pb]
    rows = await db.execute(select(LedgerEntry.id, LedgerEntry.bot_id, LedgerTransaction.bot_id)
                            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id))
    bad += [f"écriture {eid} : transaction d'un autre bot" for eid, eb, tb in rows if eb != tb]
    return _ok("R2-05", "aucune donnée partagée entre bots", bad)


async def r2_06_audits(db: AsyncSession) -> Check:
    rows = (await db.scalars(select(AuditEvent).where(AuditEvent.action.in_(GOVERNED)))).all()
    bad = [f"{a.action} {a.entity_type} {a.entity_id} : acteur ou motif manquant"
           for a in rows if not (a.actor and (a.reason or "").strip())]
    return _ok("R2-06", "changements audités avec acteur et motif", bad, audited=len(rows))


def _days(first: datetime | None, last: datetime | None) -> Decimal | None:
    if first is None or last is None:
        return None
    return Decimal(int((last - first).total_seconds())) / Decimal(86400)


async def r2_07_history(db: AsyncSession, now: datetime) -> Check:
    m_first, m_last = (await db.execute(select(func.min(TradeMinute.minute_start),
                                               func.max(TradeMinute.minute_start)))).one()
    s_first, s_last, s_count = (await db.execute(select(func.min(BookSnapshot.minute_start),
                                                        func.max(BookSnapshot.minute_start),
                                                        func.count()))).one()
    pairs = int(await db.scalar(select(func.count()).select_from(Instrument).where(Instrument.in_universe)) or 0)
    m_days, s_days = _days(m_first, m_last), _days(s_first, s_last)
    coverage = None
    if s_first is not None and pairs:
        minutes = int((s_last - s_first).total_seconds() // 60) + 1
        coverage = Decimal(s_count) / Decimal(minutes * pairs) * 100
    eligible = None if s_first is None else s_first + timedelta(days=GATE_DAYS)

    def d(v: Decimal | None) -> str:
        return "—" if v is None else f"{v:.1f}"
    details = [
        f"minutes de trades : {d(m_days)} jours"
        + ("" if m_first is None else f" ({m_first:%Y-%m-%d} → {m_last:%Y-%m-%d})"),
        f"instantanés de carnet : {d(s_days)} jours"
        + ("" if s_first is None else f" ({s_first:%Y-%m-%d} → {s_last:%Y-%m-%d})")
        + f", couverture moyenne {d(coverage)} % sur {pairs} paires",
        "gate M2 possible à partir du " + ("—" if eligible is None else
                                          f"{eligible:%Y-%m-%d}" + (" (atteint)" if eligible <= now else "")),
    ]
    return Check("R2-07", f"historique disponible ({GATE_DAYS} jours d'instantanés pour le gate)", "INFO", False,
                 details, {"minutes_days": m_days, "snapshot_days": s_days, "snapshot_coverage_pct": coverage,
                           "gate_eligible_from": eligible})


async def r2_08_pending(db: AsyncSession, now: datetime) -> Check:
    rows = (await db.scalars(select(RiskPolicyVersion).where(
        RiskPolicyVersion.publish_requested_at.is_not(None),
        RiskPolicyVersion.publish_requested_at < now - timedelta(hours=24)))).all()
    details = [f"{v.policy_id} v{v.version} : demandée le {v.publish_requested_at:%Y-%m-%d %H:%M}, "
               f"confirmable depuis {(v.publish_requested_at + REVIEW_DELAY):%H:%M}" for v in rows if v.status.value == "DRAFT"]
    return Check("R2-08", "publications de policy en attente depuis plus de 24 h", "INFO", False,
                 details or ["aucune"], {"pending": len(details)})


async def r2_09_estimated(db: AsyncSession, now: datetime) -> Check:
    rows = (await db.scalars(select(DomainEvent.payload).where(
        DomainEvent.event_type == "EQUITY_UPDATED", DomainEvent.occurred_at >= now - timedelta(hours=24)))).all()
    with_value = [p for p in rows if p.get("positions_value_estimated") is not None]
    estimated = [p for p in with_value if Decimal(str(p["positions_value_estimated"])) > ZERO]
    share = None if not with_value else Decimal(len(estimated)) / Decimal(len(with_value)) * 100
    return Check("R2-09", "valorisation au-delà du carnet visible (ADR-022), 24 h", "INFO", False,
                 [f"{len(estimated)} instantanés sur {len(with_value)} ont une part estimée"
                  + ("" if share is None else f" ({share:.1f} %)")],
                 {"snapshots": len(with_value), "with_estimate": len(estimated), "share_pct": share})


async def r2_10_bots(db: AsyncSession, now: datetime) -> Check:
    details, data = [], {}
    since = now - timedelta(days=7)
    for b in (await db.scalars(select(Bot).order_by(Bot.name))).all():
        sv = await db.get(StrategyVersion, b.strategy_version_id)
        nets = (await db.scalars(select(Position.realized_pnl_net).where(
            Position.bot_id == b.id, Position.status == PositionStatus.CLOSED,
            Position.closed_at >= since))).all()
        net = sum((n for n in nets if n is not None), ZERO)
        details.append(f"{b.name} : {b.status.value}, {sv.strategy_id} v{sv.version}, "
                       f"{len(nets)} trades sur 7 jours, net {net:.2f} USD")
        data[b.name] = {"status": b.status.value, "version": sv.version, "trades": len(nets), "net_usd": net}
    return Check("R2-10", "bots sur 7 jours", "INFO", False, details or ["aucun bot"], data)


async def build_report_m2(sessions: async_sessionmaker[AsyncSession], *,
                          now: datetime | None = None) -> ReportM2:
    now = now or utcnow()
    async with sessions() as db:
        checks = [
            await r2_01_hashes(db), await r2_02_sessions(db), await r2_03_bots(db),
            await r2_04_books(sessions, db), await r2_05_isolation(db), await r2_06_audits(db),
            await r2_07_history(db, now), await r2_08_pending(db, now), await r2_09_estimated(db, now),
            await r2_10_bots(db, now),
        ]
        await db.rollback()
    return ReportM2(generated_at=now, checks=checks)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — recette du jalon M2 (lecture seule)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    from app.core.database import SessionLocal, engine

    async def _run() -> ReportM2:
        try:
            return await build_report_m2(SessionLocal)
        finally:
            await engine.dispose()

    report = asyncio.run(_run())
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False) if args.json else report.as_text())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
