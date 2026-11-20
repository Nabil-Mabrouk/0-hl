"""M2.2 backtest — the real DecisionEngine replayed on recorded data (spec 09-recherche/04).

Usage (production): docker exec 0-hl_worker python -m app.domain.trading.research.backtest
[--strategy FILE|id@version] [--policy FILE|id@version] [--from DATE] [--to DATE]
[--capital 10000] [--pairs BTC/USD,…] [--split 0.6] [--fee-multiplier 1] [--json] [--out FILE]

Read-only on the production database: minutes and book snapshots are copied into an
in-memory SQLite sandbox one closed minute at a time, so the engine cannot see the future.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.core.models  # noqa: F401  (users table, FK target of the domain schema)
import app.domain.models  # noqa: F401  (hl_* tables)
from app.core.database import Base
from app.domain.trading.agents.engine import DecisionEngine, EngineConfig
from app.domain.trading.agents.features import MINUTE, MinuteData
from app.domain.trading.agents.sim import PAPER_TAKER_FEE
from app.domain.trading.audit import system_actor
from app.domain.trading.bots import create_bot, transition_bot
from app.domain.trading.config_schemas import validate_risk_policy_config, validate_strategy_config
from app.domain.trading.enums import (
    BotStatus,
    CashMovementKind,
    Environment,
    IntentPurpose,
    PolicyVersionStatus,
    PositionStatus,
)
from app.domain.trading.ledger import record_cash_movement
from app.domain.trading.market.book import OrderBook
from app.domain.trading.market.state import LIVE, STALE, FeedInfo, InstrumentRules, MarketState
from app.domain.trading.models import (
    BookSnapshot,
    CashMovement,
    DomainEvent,
    EquitySnapshot,
    Fill,
    Instrument,
    LedgerTransaction,
    Order,
    OrderIntent,
    Position,
    RiskPolicyVersion,
    StrategyVersion,
    TradeMinute,
)
from app.domain.trading.research.strat01_study import (
    Bar,
    ExitRule,
    Stats,
    compute_signals,
    run_pair,
)
from app.domain.trading.types import sha256_hex, to_jsonable
from app.domain.trading.versioning import (
    create_policy_version,
    create_strategy_version,
    publish_policy_version,
    publish_strategy_version,
)

D = Decimal
ZERO = D(0)
HUNDRED = D(100)
BT_ACTOR = system_actor("backtest")
BT_BOT = "backtest"
SETTLE = timedelta(seconds=30)        # EngineConfig.window_settle_seconds
HISTORY = MINUTE * 70                 # what the SCANNER reads before a window end
DEFAULT_DAYS = 30
KNOWN_GAPS = (
    "un tick par minute (le bot : toutes les 5 s)",
    "carnet capturé jusqu'à ~25 s avant la décision",
    "aucune variation de prix entre deux instantanés (stop/objectif touchés puis repassés non vus)",
    "statut `online` et volume 24 h figés à leur valeur actuelle",
    "univers figé aux paires choisies",
)


class BacktestError(Exception):
    """A user-facing error (bad argument, no data)."""


# ---------------------------------------------------------------- inputs

@dataclass(frozen=True)
class InstrumentInfo:
    id: str
    symbol: str
    base: str
    quote: str
    pair_decimals: int
    lot_decimals: int
    tick_size: Decimal | None
    ordermin: Decimal | None
    costmin: Decimal | None
    volume_24h_usd: Decimal | None


@dataclass(frozen=True)
class MinuteRec:
    minute_start: datetime
    source: str
    notional_usd: Decimal
    buy_notional_usd: Decimal | None
    sell_notional_usd: Decimal | None
    buy_count: int | None
    sell_count: int | None
    trade_count: int
    volume_base: Decimal
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None


@dataclass(frozen=True)
class SnapRec:
    best_bid: Decimal
    best_ask: Decimal
    bids: list[tuple[Decimal, Decimal]]
    asks: list[tuple[Decimal, Decimal]]


@dataclass
class Dataset:
    start: datetime
    end: datetime
    instruments: list[InstrumentInfo]
    minutes: dict[str, list[MinuteRec]] = field(default_factory=dict)      # sorted by time
    snapshots: dict[str, dict[datetime, SnapRec]] = field(default_factory=dict)

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for inst in sorted(self.instruments, key=lambda i: i.id):
            h.update(inst.id.encode())
            for r in self.minutes.get(inst.id, []):
                h.update(json.dumps(to_jsonable([r.minute_start, r.source, r.notional_usd,
                                                 r.buy_notional_usd, r.trade_count, r.open, r.high,
                                                 r.low, r.close])).encode())
            snaps = self.snapshots.get(inst.id, {})
            for m in sorted(snaps):
                s = snaps[m]
                h.update(json.dumps(to_jsonable([m, s.bids, s.asks])).encode())
        return h.hexdigest()


def _levels(raw: list[Any]) -> list[tuple[Decimal, Decimal]]:
    return [(D(p), D(q)) for p, q in raw]


async def load_dataset(db: AsyncSession, *, start: datetime | None, end: datetime | None,
                       pairs: list[str] | None) -> Dataset:
    """Pairs with at least one book snapshot in the period; minutes from 70 min earlier."""
    if end is None:
        last = await db.scalar(select(func.max(BookSnapshot.minute_start)))
        if last is None:
            raise BacktestError("aucun instantané de carnet (hl_book_snapshot) : M2.1 est-il déployé ?")
        end = last + MINUTE
    if start is None:
        first = await db.scalar(select(func.min(BookSnapshot.minute_start)))
        if first is None:
            raise BacktestError("aucun instantané de carnet (hl_book_snapshot) : M2.1 est-il déployé ?")
        start = max(first, end - timedelta(days=DEFAULT_DAYS))
    if start >= end:
        raise BacktestError(f"période vide : {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M}")
    stmt = select(BookSnapshot.instrument_id).where(
        BookSnapshot.minute_start >= start, BookSnapshot.minute_start < end).distinct()
    present = set((await db.scalars(stmt)).all())
    if pairs:
        wanted = {f"kraken_spot:{p.strip()}" for p in pairs if p.strip()}
        missing = sorted(wanted - present)
        if missing:
            raise BacktestError(f"aucun instantané pour : {', '.join(missing)}")
        present = wanted
    if not present:
        raise BacktestError("aucune paire avec des instantanés sur la période")
    rows = (await db.scalars(select(Instrument).where(Instrument.id.in_(present))
                             .order_by(Instrument.id))).all()
    ds = Dataset(start=start, end=end, instruments=[InstrumentInfo(
        r.id, r.symbol, r.base, r.quote, r.pair_decimals, r.lot_decimals, r.tick_size, r.ordermin,
        r.costmin, r.volume_24h_usd) for r in rows])
    for inst in ds.instruments:
        mins = (await db.scalars(select(TradeMinute).where(
            TradeMinute.instrument_id == inst.id, TradeMinute.minute_start >= start - HISTORY,
            TradeMinute.minute_start < end).order_by(TradeMinute.minute_start, TradeMinute.source))).all()
        ds.minutes[inst.id] = [MinuteRec(
            r.minute_start, r.source, r.notional_usd, r.buy_notional_usd, r.sell_notional_usd,
            r.buy_count, r.sell_count, r.trade_count, r.volume_base, r.open, r.high, r.low, r.close)
            for r in mins]
        snaps = (await db.scalars(select(BookSnapshot).where(
            BookSnapshot.instrument_id == inst.id, BookSnapshot.minute_start >= start,
            BookSnapshot.minute_start < end))).all()
        ds.snapshots[inst.id] = {s.minute_start: SnapRec(s.best_bid, s.best_ask, _levels(s.bids),
                                                         _levels(s.asks)) for s in snaps}
    return ds


async def resolve_config(db: AsyncSession, ref: str, *, kind: str) -> dict[str, Any]:
    """`FILE.json` (draft, validated by the publication schema) or `id@version` (published)."""
    path = Path(ref)
    if ref.endswith(".json") or path.is_file():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BacktestError(f"{ref} : lecture impossible ({exc})") from exc
    else:
        ident, _, version = ref.partition("@")
        model, col = ((StrategyVersion, StrategyVersion.strategy_id) if kind == "strategy"
                      else (RiskPolicyVersion, RiskPolicyVersion.policy_id))
        stmt = select(model).where(col == ident)
        if version:
            stmt = stmt.where(model.version == version)
        row = (await db.scalars(stmt.order_by(model.created_at.desc()).limit(1))).first()
        if row is None:
            raise BacktestError(f"{kind} {ref} introuvable")
        config = dict(row.config)
    config = {**config, "status": "DRAFT"} if kind == "strategy" else dict(config)
    try:
        (validate_strategy_config if kind == "strategy" else validate_risk_policy_config)(config)
    except Exception as exc:  # noqa: BLE001 — surfaced as the schema error (T-M2.2-05)
        raise BacktestError(f"{kind} {ref} invalide : {exc}") from exc
    return config


# ---------------------------------------------------------------- sandbox

def sandbox_sessions() -> tuple[Any, async_sessionmaker[AsyncSession]]:
    eng = create_async_engine("sqlite+aiosqlite:///:memory:",
                              connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(eng.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False, autoflush=False)


async def prepare_sandbox(sessions: async_sessionmaker[AsyncSession], ds: Dataset, *,
                          strategy: dict[str, Any], policy: dict[str, Any], capital: Decimal) -> None:
    async with sessions() as db:
        for i in ds.instruments:
            db.add(Instrument(id=i.id, venue="kraken_spot", symbol=i.symbol, base=i.base,
                              quote=i.quote, pair_decimals=i.pair_decimals,
                              lot_decimals=i.lot_decimals, tick_size=i.tick_size,
                              ordermin=i.ordermin, costmin=i.costmin, status="online",
                              in_universe=True, volume_24h_usd=i.volume_24h_usd))
        await db.flush()
        pv = await create_policy_version(db, config=policy, name="backtest", actor=BT_ACTOR)
        if pv.status is PolicyVersionStatus.DRAFT:
            await publish_policy_version(db, pv, actor=BT_ACTOR, reason="backtest", self_approved=True)
        sv = await create_strategy_version(db, config=strategy, name="backtest", actor=BT_ACTOR)
        await publish_strategy_version(db, sv, actor=BT_ACTOR, reason="backtest", self_approved=True)
        bot = await create_bot(db, name=BT_BOT, environment=Environment.PAPER, strategy_version=sv,
                               risk_policy_version=pv, actor=BT_ACTOR)
        await transition_bot(db, bot, BotStatus.READY, actor=BT_ACTOR, reason="backtest")
        # explicit START: autostart (ADR-013) is reserved to the seed bot since M2.4
        from app.domain.trading.agents.commands import create_command
        from app.domain.trading.enums import CommandType
        await create_command(db, bot_id=bot.id, command=CommandType.START, actor=BT_ACTOR,
                             idempotency_key=f"backtest-start:{bot.id}", reason="backtest")
        await record_cash_movement(db, bot_id=bot.id, kind=CashMovementKind.DEPOSIT, amount=capital,
                                   actor=BT_ACTOR, reason="capital du backtest")
        # simulated time, not the wall clock: a deposit dated « now » would count as a capital
        # flow after the first equity snapshot and fake a 100 % daily loss (MAX_DAILY_LOSS)
        funded_at = ds.start - MINUTE
        await db.execute(update(CashMovement).values(occurred_at=funded_at))
        await db.execute(update(LedgerTransaction).values(occurred_at=funded_at))
        await db.commit()


class MinuteStore:
    """Same contract as `features.load_minutes` (live wins over backfill), but only minutes
    strictly before `visible_before` exist: the replay advances it one closed minute at a time."""

    def __init__(self, ds: Dataset) -> None:
        self.visible_before = ds.start
        self._data: dict[str, dict[datetime, MinuteData]] = {}
        for iid, rows in ds.minutes.items():
            d: dict[datetime, MinuteData] = {}
            for r in rows:
                if r.minute_start in d and d[r.minute_start].source == "live":
                    continue
                d[r.minute_start] = MinuteData(r.notional_usd, r.buy_notional_usd, r.close, r.source,
                                               r.trade_count)
            self._data[iid] = d

    async def __call__(self, _db: Any, instrument_id: str, *, start: datetime,
                       end: datetime) -> dict[datetime, MinuteData]:
        d = self._data.get(instrument_id, {})
        out: dict[datetime, MinuteData] = {}
        m, stop = start, min(end, self.visible_before)
        while m < stop:
            md = d.get(m)
            if md is not None:
                out[m] = md
            m += MINUTE
        return out


class SimClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:      # paper latency: time passes, book stays
        self.now += timedelta(seconds=seconds)


def market_for(ds: Dataset) -> MarketState:
    st = MarketState()
    for i in ds.instruments:
        st.rules[i.symbol] = InstrumentRules(
            symbol=i.symbol, instrument_id=i.id, status="online", price_precision=i.pair_decimals,
            qty_precision=i.lot_decimals, price_increment=i.tick_size, qty_min=i.ordermin,
            cost_min=i.costmin)
        st.books[i.symbol] = OrderBook(i.symbol, depth=10, price_precision=i.pair_decimals,
                                       qty_precision=i.lot_decimals)
        st.feeds[i.symbol] = FeedInfo(state=STALE, reason="NO_SNAPSHOT")
    st.connection_alive = True
    st.connection_reason = None
    return st


def apply_snapshots(st: MarketState, ds: Dataset, minute: datetime) -> None:
    """Book of minute `m` (captured within ~5 s after m); no snapshot ⇒ STALE (NO_SNAPSHOT)."""
    for i in ds.instruments:
        snap = ds.snapshots.get(i.id, {}).get(minute)
        if snap is None:
            st.feeds[i.symbol] = FeedInfo(state=STALE, reason="NO_SNAPSHOT")
            continue
        st.books[i.symbol].apply(
            "snapshot", [{"price": p, "qty": q} for p, q in snap.bids],
            [{"price": p, "qty": q} for p, q in snap.asks])
        st.feeds[i.symbol] = FeedInfo(state=LIVE, reason=None)


# ---------------------------------------------------------------- run

@dataclass
class TradeOut:
    instrument_id: str
    opened_at: datetime
    closed_at: datetime | None
    entry_price: Decimal | None
    entry_notional: Decimal          # Σ qty × price of the entry fills
    exit_notional: Decimal           # Σ qty × price of the exit fills
    realized_pnl_net: Decimal | None
    fees_paid: Decimal
    exit_reason: str | None

    @property
    def net_pct(self) -> Decimal | None:
        if self.realized_pnl_net is None or not self.entry_notional:
            return None
        return self.realized_pnl_net / self.entry_notional

    def as_json(self) -> dict[str, Any]:
        return to_jsonable({"instrument_id": self.instrument_id, "opened_at": self.opened_at,
                            "closed_at": self.closed_at, "entry_price": self.entry_price,
                            "entry_notional": self.entry_notional,
                            "exit_notional": self.exit_notional, "realized_pnl_net": self.realized_pnl_net,
                            "fees_paid": self.fees_paid, "exit_reason": self.exit_reason})


@dataclass
class PartStats:
    trades: int = 0
    wins: int = 0
    net: Decimal = ZERO
    fees: Decimal = ZERO
    gains: Decimal = ZERO
    losses: Decimal = ZERO
    net_pct_sum: Decimal = ZERO
    exits: Counter = field(default_factory=Counter)

    @classmethod
    def of(cls, trades: list[TradeOut]) -> PartStats:
        st = cls()
        for t in trades:
            if t.realized_pnl_net is None:
                continue
            st.trades += 1
            st.net += t.realized_pnl_net
            st.fees += t.fees_paid
            st.net_pct_sum += t.net_pct or ZERO
            if t.realized_pnl_net > ZERO:
                st.wins += 1
                st.gains += t.realized_pnl_net
            else:
                st.losses -= t.realized_pnl_net
            st.exits[t.exit_reason or "?"] += 1
        return st

    def as_json(self) -> dict[str, Any]:
        return {"trades": self.trades, "wins": self.wins,
                "win_rate_pct": None if not self.trades else f"{D(self.wins) / self.trades * HUNDRED:.1f}",
                "net_usd": f"{self.net:.2f}", "fees_usd": f"{self.fees:.2f}",
                "mean_net_pct": None if not self.trades else f"{self.net_pct_sum / self.trades * HUNDRED:.3f}",
                "profit_factor": None if not self.losses else f"{self.gains / self.losses:.2f}",
                "exits": dict(sorted(self.exits.items()))}


@dataclass
class BacktestReport:
    header: dict[str, Any]
    capital: Decimal
    final_equity: Decimal | None
    max_drawdown_pct: Decimal | None
    overall: PartStats
    first: PartStats
    second: PartStats
    rejections: dict[str, int]
    top_pairs: list[tuple[str, Decimal]]
    baseline: dict[str, Any]
    trades: list[TradeOut]
    drawdown_at: dict[str, Any] | None = None

    def as_json(self, *, with_trades: bool = True) -> dict[str, Any]:
        out = {"header": self.header, "known_gaps": list(KNOWN_GAPS),
               "capital": f"{self.capital:.2f}",
               "final_equity": None if self.final_equity is None else f"{self.final_equity:.2f}",
               "max_drawdown_pct": None if self.max_drawdown_pct is None else f"{self.max_drawdown_pct:.2f}",
               "max_drawdown_at": self.drawdown_at,
               "overall": self.overall.as_json(), "first_part": self.first.as_json(),
               "second_part": self.second.as_json(), "rejections": self.rejections,
               "top_pairs": [{"instrument_id": i, "net_usd": f"{n:.2f}"} for i, n in self.top_pairs],
               "baseline": self.baseline}
        if with_trades:
            out["trades"] = [t.as_json() for t in self.trades]
        return out

    def as_text(self) -> str:
        h = self.header

        def line(name: str, s: PartStats) -> str:
            j = s.as_json()
            return (f"  {name:<9} trades {j['trades']:>4}  gain {j['win_rate_pct'] or '—':>5}%  "
                    f"net {j['net_usd']:>10} USD  frais {j['fees_usd']:>9}  "
                    f"moyen {j['mean_net_pct'] or '—':>7}%  PF {j['profit_factor'] or '—':>5}  {j['exits']}")

        b = self.baseline
        lines = [
            "0-hl — backtest M2.2 (09-recherche/04, lecture seule)",
            f"période {h['start']} → {h['end']} UTC, coupure {h['split_at']}",
            f"stratégie {h['strategy']} (sha {h['strategy_sha256'][:12]}), "
            f"policy {h['policy']} (sha {h['policy_sha256'][:12]})",
            f"{len(h['pairs'])} paires, {h['minutes']} minutes rejouées, frais {h['taker_fee_pct']} %, "
            f"données sha {h['dataset_sha256'][:12]}, code {h['git_sha']}",
            "",
            f"capital {self.capital:.2f} → "
            f"{'—' if self.final_equity is None else f'{self.final_equity:.2f}'} USD, "
            f"drawdown max {'—' if self.max_drawdown_pct is None else f'{self.max_drawdown_pct:.2f}'} %"
            + ("" if not self.drawdown_at else
               f" (au plus bas le {self.drawdown_at['at']} : equity {self.drawdown_at['equity']} = cash "
               f"{self.drawdown_at['cash']} + réservé {self.drawdown_at['cash_reserved']} + positions "
               f"{self.drawdown_at['positions_value'] or '—'})"),
            line("total", self.overall), line("partie 1", self.first), line("partie 2", self.second),
            "",
            "entrées refusées : " + (", ".join(f"{k}={v}" for k, v in self.rejections.items()) or "—"),
            "meilleures paires : " + (", ".join(f"{i.split(':', 1)[1]} {n:+.2f}"
                                                for i, n in self.top_pairs) or "—"),
            f"référence sans filtre ({b['exit']}) : partie 1 net moyen "
            f"{b['first_part']['mean_net_pct'] or '—'} % sur {b['first_part']['trades']} trades, "
            f"partie 2 {b['second_part']['mean_net_pct'] or '—'} % sur {b['second_part']['trades']}",
            "",
            "écarts connus avec le bot : " + " ; ".join(KNOWN_GAPS),
        ]
        return "\n".join(lines)


def _git_sha() -> str:
    return os.environ.get("GIT_SHA") or "unknown"


def _baseline(ds: Dataset, strategy: dict[str, Any], cut: datetime) -> dict[str, Any]:
    ex = strategy["exit"]
    hold = int(ex.get("max_hold_seconds") or 3600) // 60
    tp = ex.get("take_profit_r")
    rule = ExitRule(max(hold, 1), D(str(ex["initial_stop_pct"])), D(str(tp)) if tp else None)
    trades = []
    for inst in ds.instruments:
        mins: dict[datetime, MinuteData] = {}
        bars: dict[datetime, Bar] = {}
        for r in ds.minutes.get(inst.id, []):
            if r.minute_start in mins and mins[r.minute_start].source == "live":
                continue
            mins[r.minute_start] = MinuteData(r.notional_usd, r.buy_notional_usd, r.close, r.source,
                                              r.trade_count)
            bars[r.minute_start] = Bar(r.open, r.high, r.low, r.close)
        trades += run_pair(inst.id, bars, compute_signals(mins, ds.start, ds.end), rule, ds.end)
    return {"exit": rule.label,
            "first_part": Stats.of(t for t in trades if t.at < cut).as_json(),
            "second_part": Stats.of(t for t in trades if t.at >= cut).as_json()}


async def run_backtest(ds: Dataset, *, strategy: dict[str, Any], policy: dict[str, Any],
                       strategy_ref: str = "fichier", policy_ref: str = "fichier",
                       capital: Decimal = D(10000), split: Decimal = D("0.6"),
                       fee_multiplier: Decimal = D(1)) -> BacktestReport:
    eng, sessions = sandbox_sessions()
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await prepare_sandbox(sessions, ds, strategy=strategy, policy=policy, capital=capital)
        clock = SimClock(ds.start + SETTLE)
        fee = PAPER_TAKER_FEE * fee_multiplier
        market = market_for(ds)
        store = MinuteStore(ds)
        engine = DecisionEngine(sessions, market, config=EngineConfig(taker_fee=fee),
                                clock=clock, sleep=clock.sleep, minutes_loader=store)
        minute, steps = ds.start, 0
        while minute < ds.end:
            # 1. closed minutes only (INV-03): everything strictly before `minute`
            store.visible_before = minute
            # 2. book of the minute, 3. one tick at m + 30 s
            apply_snapshots(market, ds, minute)
            clock.now = max(clock.now, minute + SETTLE)
            await engine.tick()
            minute += MINUTE
            steps += 1
        return await _report(sessions, ds, strategy=strategy, policy=policy,
                             strategy_ref=strategy_ref, policy_ref=policy_ref, capital=capital,
                             split=split, fee=fee, steps=steps)
    finally:
        await eng.dispose()


async def _report(sessions, ds: Dataset, *, strategy, policy, strategy_ref, policy_ref, capital,
                  split, fee, steps) -> BacktestReport:
    cut = ds.start + (ds.end - ds.start) * float(split)
    cut = cut.replace(microsecond=0)
    async with sessions() as db:
        positions = (await db.scalars(select(Position).where(Position.opened_at.is_not(None))
                                      .order_by(Position.opened_at, Position.instrument_id))).all()
        notional: dict[tuple[Any, IntentPurpose], Decimal] = defaultdict(lambda: ZERO)
        rows = await db.execute(select(OrderIntent.position_id, OrderIntent.purpose, Fill.qty, Fill.price)
                                .join(Order, Order.intent_id == OrderIntent.id)
                                .join(Fill, Fill.order_id == Order.id))
        for pid, purpose, qty, price in rows:
            notional[(pid, purpose)] += qty * price
        trades = [TradeOut(p.instrument_id, p.opened_at, p.closed_at, p.avg_entry_price,
                           notional[(p.id, IntentPurpose.ENTRY)], notional[(p.id, IntentPurpose.EXIT)],
                           p.realized_pnl_net if p.status is PositionStatus.CLOSED else None,
                           p.fees_paid, p.exit_reason) for p in positions]
        snaps = [s for s in (await db.scalars(select(EquitySnapshot).order_by(EquitySnapshot.at))).all()
                 if s.equity is not None]
        equities = [s.equity for s in snaps]
        rejected = (await db.scalars(select(DomainEvent).where(
            DomainEvent.event_type == "RISK_REJECTED"))).all()
    rejections: Counter = Counter()
    for ev in rejected:
        p = ev.payload or {}
        for reason in (p.get("failed") or p.get("reasons") or ["?"]):
            rejections[f"{p.get('stage', '?')}:{reason}"] += 1
    peak, mdd, trough = None, None, None
    for s in snaps:
        e = s.equity
        peak = e if peak is None or e > peak else peak
        if peak and peak > ZERO:
            dd = (peak - e) / peak * HUNDRED
            if mdd is None or dd > mdd:
                mdd, trough = dd, s
    drawdown_at = None if trough is None or not mdd else {
        "at": f"{trough.at:%Y-%m-%d %H:%M}", "equity": f"{trough.equity:.2f}", "cash": f"{trough.cash:.2f}",
        "cash_reserved": f"{trough.cash_reserved:.2f}",
        "positions_value": None if trough.positions_mark_value is None else f"{trough.positions_mark_value:.2f}"}
    by_pair: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in trades:
        if t.realized_pnl_net is not None:
            by_pair[t.instrument_id] += t.realized_pnl_net
    top = sorted(by_pair.items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    header = {
        "start": f"{ds.start:%Y-%m-%d %H:%M}", "end": f"{ds.end:%Y-%m-%d %H:%M}",
        "split_at": f"{cut:%Y-%m-%d %H:%M}", "split": format(split, "f"),
        "strategy": f"{strategy_ref} ({strategy.get('strategy_id')} {strategy.get('version')})",
        "policy": f"{policy_ref} ({policy.get('policy_id')} {policy.get('version')})",
        "strategy_sha256": sha256_hex({k: v for k, v in strategy.items() if k != "status"}),
        "policy_sha256": sha256_hex(policy),
        "pairs": [i.symbol for i in ds.instruments], "minutes": steps,
        "taker_fee_pct": format((fee * HUNDRED).normalize(), "f"), "dataset_sha256": ds.fingerprint(),
        "git_sha": _git_sha(), "engine": "DecisionEngine, pas 1 min, décision à m+30 s",
    }
    return BacktestReport(
        header=header, capital=capital, final_equity=equities[-1] if equities else None,
        max_drawdown_pct=mdd, overall=PartStats.of(trades),
        first=PartStats.of([t for t in trades if t.opened_at < cut]),
        second=PartStats.of([t for t in trades if t.opened_at >= cut]),
        rejections=dict(sorted(rejections.items())), top_pairs=top,
        baseline=_baseline(ds, strategy, cut), trades=trades, drawdown_at=drawdown_at)


# ---------------------------------------------------------------- CLI

def _date(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


async def build_backtest(sessions: async_sessionmaker[AsyncSession], *, strategy_ref: str,
                         policy_ref: str | None, start: datetime | None, end: datetime | None,
                         pairs: list[str] | None, capital: Decimal, split: Decimal,
                         fee_multiplier: Decimal) -> BacktestReport:
    async with sessions() as db:
        strategy = await resolve_config(db, strategy_ref, kind="strategy")
        pref: str = policy_ref or strategy.get("risk_policy_id") or "paper-default"
        policy = await resolve_config(db, pref, kind="policy")
        ds = await load_dataset(db, start=start, end=end, pairs=pairs)
        await db.rollback()
    return await run_backtest(ds, strategy=strategy, policy=policy, strategy_ref=strategy_ref,
                              policy_ref=pref, capital=capital, split=split,
                              fee_multiplier=fee_multiplier)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="0-hl — backtest M2.2 (lecture seule)")
    parser.add_argument("--strategy", default="volume-spike@0.2.0")
    parser.add_argument("--policy", default=None)
    parser.add_argument("--from", dest="start", type=_date, default=None)
    parser.add_argument("--to", dest="end", type=_date, default=None)
    parser.add_argument("--capital", type=D, default=D(10000))
    parser.add_argument("--pairs", default=None)
    parser.add_argument("--split", type=D, default=D("0.6"))
    parser.add_argument("--fee-multiplier", type=D, default=D(1))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", default=None)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    if not args.verbose:
        logging.getLogger("hl").setLevel(logging.ERROR)  # refusals are counted in the report

    from app.core.database import SessionLocal, engine

    async def _run() -> BacktestReport:
        try:
            return await build_backtest(
                SessionLocal, strategy_ref=args.strategy, policy_ref=args.policy, start=args.start,
                end=args.end, pairs=args.pairs.split(",") if args.pairs else None,
                capital=args.capital, split=args.split, fee_multiplier=args.fee_multiplier)
        finally:
            await engine.dispose()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        report = asyncio.run(_run())
    except BacktestError as exc:
        print(f"erreur : {exc}", file=sys.stderr)
        return 2
    if args.out:
        Path(args.out).write_text(json.dumps(report.as_json(), indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    print(json.dumps(report.as_json(), indent=2, ensure_ascii=False) if args.json else report.as_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
