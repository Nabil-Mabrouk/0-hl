"""Trades and statistics — M2.5 (spec 03-ecrans/M2-trades-et-statistiques.md).

No new table: trades are closed positions enriched from their fills, their session (strategy
version at the time of the trade) and their sizing decision. Unknown values stay None.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.enums import CashMovementKind, IntentPurpose, PositionStatus, SnapshotQuality
from app.domain.trading.models import (
    BotSession,
    CashMovement,
    EquitySnapshot,
    Fill,
    Order,
    OrderIntent,
    Position,
    SizingDecision,
    StrategyVersion,
)

ZERO = Decimal(0)
HUNDRED = Decimal(100)
BPS = Decimal(10000)
MIN_SAMPLE = 20                     # « échantillon trop petit » below (validated 2026-11-20)
MAX_CURVE_POINTS = 2000
CSV_LIMIT = 10000


@dataclass
class TradeRow:
    position: Position
    strategy_version_id: uuid.UUID | None
    strategy_id: str | None
    strategy_version: str | None
    entry_notional: Decimal | None
    exit_notional: Decimal | None
    exit_qty: Decimal | None
    reference_price: Decimal | None
    correlation_id: uuid.UUID | None

    @property
    def net(self) -> Decimal | None:
        return self.position.realized_pnl_net

    @property
    def avg_exit_price(self) -> Decimal | None:
        return self.exit_notional / self.exit_qty if self.exit_notional and self.exit_qty else None

    @property
    def net_pct(self) -> Decimal | None:
        return self.net / self.entry_notional * HUNDRED if self.net is not None and self.entry_notional else None

    @property
    def r_multiple(self) -> Decimal | None:
        p = self.position
        return p.realized_pnl_net / p.r0_usd if p.realized_pnl_net is not None and p.r0_usd else None

    @property
    def hold_seconds(self) -> int | None:
        p = self.position
        return int((p.closed_at - p.opened_at).total_seconds()) if p.closed_at and p.opened_at else None

    @property
    def entry_slippage_bps(self) -> Decimal | None:
        avg, ref = self.position.avg_entry_price, self.reference_price
        return (avg - ref) / ref * BPS if avg is not None and ref else None

    def extras(self) -> dict[str, Any]:
        return {
            "strategy": None if self.strategy_version_id is None else {
                "version_id": self.strategy_version_id, "strategy_id": self.strategy_id,
                "version": self.strategy_version},
            "entry_notional": self.entry_notional, "exit_notional": self.exit_notional,
            "avg_exit_price": self.avg_exit_price, "net_pct": self.net_pct,
            "hold_seconds": self.hold_seconds, "entry_slippage_bps": self.entry_slippage_bps,
            "correlation_id": self.correlation_id,
        }


async def trade_rows(db: AsyncSession, *, bot_id: uuid.UUID | None = None, since: datetime | None = None,
                     until: datetime | None = None, instrument_id: str | None = None,
                     exit_reason: str | None = None,
                     strategy_version_id: uuid.UUID | None = None) -> list[TradeRow]:
    """Closed positions matching the filters, most recently closed first (period on closed_at)."""
    stmt = (select(Position, BotSession.strategy_version_id, StrategyVersion.strategy_id,
                   StrategyVersion.version)
            .join(BotSession, BotSession.id == Position.session_id)
            .join(StrategyVersion, StrategyVersion.id == BotSession.strategy_version_id)
            .where(Position.status == PositionStatus.CLOSED))
    if bot_id is not None:
        stmt = stmt.where(Position.bot_id == bot_id)
    if since is not None:
        stmt = stmt.where(Position.closed_at >= since)
    if until is not None:
        stmt = stmt.where(Position.closed_at < until)
    if instrument_id:
        stmt = stmt.where(Position.instrument_id == instrument_id)
    if exit_reason:
        stmt = stmt.where(Position.exit_reason == exit_reason)
    if strategy_version_id is not None:
        stmt = stmt.where(BotSession.strategy_version_id == strategy_version_id)
    rows = (await db.execute(stmt.order_by(Position.closed_at.desc(), Position.id))).all()
    if not rows:
        return []
    ids = [r[0].id for r in rows]
    notional: dict[tuple[uuid.UUID, IntentPurpose], Decimal] = defaultdict(lambda: ZERO)
    qty: dict[tuple[uuid.UUID, IntentPurpose], Decimal] = defaultdict(lambda: ZERO)
    fills = await db.execute(select(OrderIntent.position_id, OrderIntent.purpose, Fill.qty, Fill.price)
                             .join(Order, Order.intent_id == OrderIntent.id)
                             .join(Fill, Fill.order_id == Order.id)
                             .where(OrderIntent.position_id.in_(ids)))
    for pid, purpose, q, price in fills:
        notional[(pid, purpose)] += q * price
        qty[(pid, purpose)] += q
    entries = {pid: (cand, ref) for pid, cand, ref in (await db.execute(
        select(OrderIntent.position_id, OrderIntent.candidate_id, SizingDecision.reference_price)
        .outerjoin(SizingDecision, SizingDecision.id == OrderIntent.risk_approval_id)
        .where(OrderIntent.position_id.in_(ids), OrderIntent.purpose == IntentPurpose.ENTRY))).all()}
    out = []
    for p, svid, sid, sver in rows:
        e, x = (p.id, IntentPurpose.ENTRY), (p.id, IntentPurpose.EXIT)
        cand, ref = entries.get(p.id, (None, None))
        out.append(TradeRow(p, svid, sid, sver, notional[e] if e in notional else None,
                            notional[x] if x in notional else None, qty[x] if x in qty else None,
                            ref, cand))
    return out


# ---------------------------------------------------------------- statistics

def _mean(values: list[Decimal]) -> Decimal | None:
    return sum(values, ZERO) / len(values) if values else None


def compute_stats(rows: list[TradeRow]) -> dict[str, Any]:
    nets = [r.net for r in rows if r.net is not None]
    wins = [n for n in nets if n > ZERO]
    losses = [n for n in nets if n < ZERO]
    fees = sum((r.position.fees_paid for r in rows), ZERO)
    net = sum(nets, ZERO)
    gross = net + fees
    n = len(nets)
    pcts = [r.net_pct for r in rows if r.net_pct is not None]
    by_pair: dict[str, list[Decimal]] = defaultdict(list)
    for r in rows:
        if r.net is not None:
            by_pair[r.position.instrument_id].append(r.net)
    pair_net = sorted(((k, sum(v, ZERO)) for k, v in by_pair.items()), key=lambda kv: (kv[1], kv[0]))
    avg_win, avg_loss = _mean(wins), _mean(losses)
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "breakeven": n - len(wins) - len(losses),
        "win_rate_pct": None if not n else Decimal(len(wins)) / n * HUNDRED,
        "net_usd": net, "gross_usd": gross, "fees_usd": fees,
        "fees_share_of_gross_pct": None if gross <= ZERO else fees / gross * HUNDRED,
        "avg_win_usd": avg_win, "avg_loss_usd": avg_loss,
        "expectancy_usd": None if not n else net / n,
        "expectancy_pct": _mean(pcts),
        "profit_factor": None if not losses else sum(wins, ZERO) / -sum(losses, ZERO),
        "avg_r": _mean([r.r_multiple for r in rows if r.r_multiple is not None]),
        "avg_hold_seconds": None if not rows or all(r.hold_seconds is None for r in rows) else
        sum(r.hold_seconds or 0 for r in rows) // max(1, sum(1 for r in rows if r.hold_seconds is not None)),
        "avg_entry_slippage_bps": _mean([r.entry_slippage_bps for r in rows if r.entry_slippage_bps is not None]),
        "exits": dict(sorted(Counter(r.position.exit_reason or "?" for r in rows).items())),
        "best_pair": None if not pair_net else {"instrument_id": pair_net[-1][0], "net_usd": pair_net[-1][1]},
        "worst_pair": None if not pair_net else {"instrument_id": pair_net[0][0], "net_usd": pair_net[0][1]},
        "sample_too_small": n < MIN_SAMPLE,
    }


def stats_report(rows: list[TradeRow]) -> dict[str, Any]:
    versions: dict[Any, list[TradeRow]] = defaultdict(list)
    pairs: dict[str, list[TradeRow]] = defaultdict(list)
    for r in rows:
        versions[r.strategy_version_id].append(r)
        pairs[r.position.instrument_id].append(r)
    by_version = []
    for vid, rs in versions.items():
        first = rs[0]
        by_version.append({"version_id": vid, "strategy_id": first.strategy_id,
                           "version": first.strategy_version} | compute_stats(rs))
    by_version.sort(key=lambda v: (v["strategy_id"] or "", v["version"] or ""))
    by_pair = []
    for iid, rs in pairs.items():
        s = compute_stats(rs)
        by_pair.append({"instrument_id": iid, "trades": s["trades"], "net_usd": s["net_usd"],
                        "win_rate_pct": s["win_rate_pct"], "fees_usd": s["fees_usd"]})
    by_pair.sort(key=lambda p: (p["net_usd"], p["instrument_id"]))
    overall = compute_stats(rows)
    return {"overall": overall, "by_version": by_version, "by_pair": by_pair,
            "by_exit_reason": overall["exits"]}


# ---------------------------------------------------------------- curve

async def performance_curve(db: AsyncSession, bot_id: uuid.UUID, *, since: datetime | None = None,
                            until: datetime | None = None,
                            max_points: int = MAX_CURVE_POINTS) -> dict[str, Any]:
    """P = equity − capital contributed at that time (deposits/withdrawals excluded), OK snapshots
    only; max drawdown as in EQUITY (06-agents/03): (max P − P) / (capital + max P)."""
    flows = (await db.execute(select(CashMovement.occurred_at, CashMovement.kind, CashMovement.amount)
                              .where(CashMovement.bot_id == bot_id)
                              .order_by(CashMovement.occurred_at))).all()
    stmt = select(EquitySnapshot).where(EquitySnapshot.bot_id == bot_id,
                                        EquitySnapshot.quality == SnapshotQuality.OK,
                                        EquitySnapshot.equity.is_not(None))
    if since is not None:
        stmt = stmt.where(EquitySnapshot.at >= since)
    if until is not None:
        stmt = stmt.where(EquitySnapshot.at < until)
    snaps = (await db.scalars(stmt.order_by(EquitySnapshot.at))).all()
    points: list[dict[str, Any]] = []
    capital, k = ZERO, 0
    peak: Decimal | None = None
    mdd: Decimal | None = None
    trough: dict[str, Any] | None = None
    for s in snaps:
        while k < len(flows) and flows[k][0] <= s.at:
            capital += flows[k][2] if flows[k][1] is CashMovementKind.DEPOSIT else -flows[k][2]
            k += 1
        perf = s.equity - capital  # type: ignore[operator]
        point = {"at": s.at, "performance": perf, "equity": s.equity, "capital": capital}
        points.append(point)
        top: Decimal = perf if peak is None or perf > peak else peak
        peak = top
        base = capital + top
        if base > ZERO:
            dd = (top - perf) / base * HUNDRED
            if mdd is None or dd > mdd:
                mdd, trough = dd, point
    total = len(points)
    if total > max_points:
        step = -(-total // max_points)
        kept = points[::step]
        if points[-1] is not kept[-1]:
            kept.append(points[-1])
        if trough is not None and all(p is not trough for p in kept):
            kept.append(trough)
            kept.sort(key=lambda p: p["at"])
        points = kept
    return {"points": points, "total_points": total, "max_drawdown_pct": mdd,
            "max_drawdown_at": None if trough is None or not mdd else trough["at"]}


# ---------------------------------------------------------------- CSV

CSV_COLUMNS = ("closed_at", "opened_at", "bot_id", "instrument_id", "strategy_id", "strategy_version",
               "quantity_entered", "avg_entry_price", "avg_exit_price", "entry_notional", "fees_paid",
               "realized_pnl_net", "net_pct", "r_multiple", "exit_reason", "hold_seconds",
               "entry_slippage_bps", "position_id")


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, datetime):
        return v.isoformat()
    return str(v)


def trades_csv(rows: list[TradeRow]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";", lineterminator="\n")
    w.writerow(CSV_COLUMNS)
    for r in rows[:CSV_LIMIT]:
        p = r.position
        qty_in = r.entry_notional / p.avg_entry_price if r.entry_notional and p.avg_entry_price else None
        w.writerow([_cell(x) for x in (
            p.closed_at, p.opened_at, p.bot_id, p.instrument_id, r.strategy_id, r.strategy_version,
            qty_in, p.avg_entry_price, r.avg_exit_price, r.entry_notional, p.fees_paid,
            p.realized_pnl_net, r.net_pct, r.r_multiple, p.exit_reason, r.hold_seconds,
            r.entry_slippage_bps, p.id)])
    return buf.getvalue()


async def count_open(db: AsyncSession, bot_id: uuid.UUID | None) -> int:
    stmt = select(func.count()).select_from(Position).where(Position.status == PositionStatus.OPEN)
    if bot_id is not None:
        stmt = stmt.where(Position.bot_id == bot_id)
    return int(await db.scalar(stmt) or 0)
