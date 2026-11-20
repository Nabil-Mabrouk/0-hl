"""DecisionEngine — the M1.3 paper decision loop (spec 06-agents/03 « Boucle de décision »).

Every tick (5 s): commands → exits (all bots, always) → entries (RUNNING bots)
→ EQUITY (snapshots, limits, reconciliation). Each step runs in its own
transaction; a failing step is logged and never stops position protection.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.trading.agents import commands as cmds
from app.domain.trading.agents.equity import (
    compute_equity,
    daily_loss,
    liquidation_value,
    drawdown,
    realized_pnl_net,
    reconcile,
    write_snapshot,
)
from app.domain.trading.agents.executor import execute_entry, execute_exit
from app.domain.trading.agents.features import (
    MINUTE,
    Feature,
    evaluate_conditions,
    load_minutes,
    spread_feature,
    window_features,
)
from app.domain.trading.agents.gate import evaluate_market_gate
from app.domain.trading.agents.sim import PAPER_LATENCY_SECONDS, PAPER_TAKER_FEE
from app.domain.trading.agents.sizing import SizingInputs, size_position
from app.domain.trading.audit import system_actor
from app.domain.trading.bots import transition_bot
from app.domain.trading.config_schemas import RiskPolicyConfig, validate_risk_policy_config, validate_strategy_config
from app.domain.trading.enums import (
    AssessmentVerdict,
    BotStatus,
    CandidateStatus,
    CommandType,
    PositionStatus,
    SessionStatus,
)
from app.domain.trading.events import append_event
from app.domain.trading.market.state import MarketState
from app.domain.trading.models import (
    Bot,
    Candidate,
    Instrument,
    OrderIntent,
    Position,
    RiskAssessment,
    RiskPolicyVersion,
    SizingDecision,
    StrategyVersion,
)
from app.domain.trading.types import sha256_hex, utcnow

log = logging.getLogger("hl.agents")
SOURCE = "worker:agents"
WORKER_ACTOR = system_actor("worker")
ZERO = Decimal(0)


@dataclass
class EngineConfig:
    tick_seconds: float = 5.0
    latency_seconds: float = PAPER_LATENCY_SECONDS
    snapshot_seconds: float = 60.0
    reconcile_seconds: float = 300.0
    summary_seconds: float = 300.0
    window_settle_seconds: float = 30.0
    taker_fee: Decimal = PAPER_TAKER_FEE  # paper venue fee; the backtest may stress it (09-recherche/04)


@dataclass
class _Counters:
    evaluated: int = 0
    unknown: int = 0
    candidates: int = 0
    rejected: int = 0
    entries: int = 0
    exits: int = 0
    fails: Counter = field(default_factory=Counter)
    unknown_reasons: Counter = field(default_factory=Counter)


@dataclass
class _BotRuntime:
    last_window: dict[str, datetime] = field(default_factory=dict)
    last_snapshot: datetime | None = None
    last_reconcile: datetime | None = None
    deferred_logged: set[tuple[uuid.UUID, str]] = field(default_factory=set)
    counters: _Counters = field(default_factory=_Counters)
    cycles: dict[datetime, _Counters] = field(default_factory=dict)
    emitted_cycles: set[datetime] = field(default_factory=set)
    estimated: Decimal = ZERO          # ADR-022: positions value counted at the exit floor

    def count(self, window_end: datetime, name: str, n: int = 1) -> None:
        for c in (self.counters, self.cycles.setdefault(window_end, _Counters())):
            setattr(c, name, getattr(c, name) + n)

    def count_map(self, window_end: datetime, name: str, keys: list[str]) -> None:
        for c in (self.counters, self.cycles.setdefault(window_end, _Counters())):
            getattr(c, name).update(keys)


class DecisionEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        market: MarketState,
        *,
        config: EngineConfig | None = None,
        clock: Callable[[], datetime] = utcnow,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        minutes_loader: Callable[..., Awaitable[dict[datetime, Any]]] = load_minutes,
    ) -> None:
        self._sessions = session_factory
        self._load_minutes = minutes_loader  # the backtest reads closed minutes from memory (09-recherche/04)
        self.market = market
        self.config = config or EngineConfig()
        self._now = clock
        self._sleep = sleep
        self._runtime: dict[uuid.UUID, _BotRuntime] = {}
        self._last_summary: datetime | None = None
        self._configs: dict[uuid.UUID, Any] = {}

    # ------------------------------------------------------------------ #
    async def run(self, stop: asyncio.Event) -> None:
        log.info("decision engine started (tick %.0fs)", self.config.tick_seconds)
        while not stop.is_set():
            await self.tick(stop)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.config.tick_seconds)
            except asyncio.TimeoutError:
                pass
        log.info("decision engine stopped")

    async def tick(self, stop: asyncio.Event | None = None) -> None:
        for name, step in (("commands", self.step_commands), ("exits", self.step_exits),
                           ("entries", self.step_entries), ("equity", self.step_equity)):
            if stop is not None and stop.is_set():
                return
            try:
                await step()
            except Exception:  # noqa: BLE001 — one failing step never stops the others
                log.exception("decision step %s failed", name)
        self._maybe_summary()

    def _rt(self, bot_id: uuid.UUID) -> _BotRuntime:
        return self._runtime.setdefault(bot_id, _BotRuntime())

    async def _strategy(self, db: AsyncSession, version_id: uuid.UUID):
        if version_id not in self._configs:
            sv = await db.get(StrategyVersion, version_id)
            self._configs[version_id] = validate_strategy_config({**sv.config, "status": sv.status.value})
        return self._configs[version_id]

    async def _policy(self, db: AsyncSession, version_id: uuid.UUID) -> RiskPolicyConfig:
        if version_id not in self._configs:
            pv = await db.get(RiskPolicyVersion, version_id)
            self._configs[version_id] = validate_risk_policy_config(pv.config)
        return self._configs[version_id]

    async def _emit(self, db, event_type, aggregate_type, aggregate_id, payload, *, bot_id, corr):
        await append_event(db, event_type=event_type, aggregate_type=aggregate_type,
                           aggregate_id=aggregate_id, payload=payload, source=SOURCE,
                           bot_id=bot_id, correlation_id=corr)

    # ------------------------------------------------------------------ #
    # 1. commands
    # ------------------------------------------------------------------ #
    async def step_commands(self) -> None:
        async with self._sessions() as db:
            await cmds.ensure_autostart(db)
            await cmds.process_pending_commands(db, self._now())
            await db.commit()

    # ------------------------------------------------------------------ #
    # 2. exits (every open position, whatever the bot status)
    # ------------------------------------------------------------------ #
    def _exit_reason(self, p: Position, bot: Bot, emergency: bool, max_hold: int | None,
                     exit_on_status: bool, now: datetime) -> str | None:
        symbol = p.instrument_id.split(":", 1)[1]
        if emergency:
            return "EMERGENCY"
        rules = self.market.rules.get(symbol)
        if exit_on_status and rules is not None and rules.status != "online":
            return "STATUS"
        book = self.market.book(symbol)
        if book is not None and self.market.is_fresh(symbol) and book.best_bid is not None:
            bid = book.best_bid[0]
            if p.initial_stop_price is not None and bid <= p.initial_stop_price:
                return "STOP"
            if p.take_profit_price is not None and bid >= p.take_profit_price:
                return "TAKE_PROFIT"
        if max_hold and p.opened_at and now - p.opened_at >= timedelta(seconds=max_hold):
            return "TIME"
        return None

    async def step_exits(self) -> None:
        async with self._sessions() as db:
            positions = (await db.scalars(select(Position).where(
                Position.status.in_((PositionStatus.OPEN, PositionStatus.REDUCING))))).all()
            ids = [(p.id, p.bot_id) for p in positions]
        for position_id, bot_id in ids:
            async with self._sessions() as db:
                p = await db.get(Position, position_id)
                bot = await db.get(Bot, bot_id)
                strat = await self._strategy(db, bot.strategy_version_id)
                emergency = (bot.status is BotStatus.STOPPING and
                             await cmds.last_stop_command(db, bot.id) is CommandType.EMERGENCY_STOP)
                now = self._now()
                reason = self._exit_reason(p, bot, emergency, strat.exit.max_hold_seconds,
                                           bool(strat.exit.exit_on_status_change), now)
                if reason is None:
                    continue
                symbol = p.instrument_id.split(":", 1)[1]
                rules = self.market.rules.get(symbol)
                out = await execute_exit(
                    db, position=p, exit_reason=reason, book=self.market.book(symbol),
                    book_fresh=self.market.is_fresh(symbol),
                    pair_status=rules.status if rules else None, now=now,
                    fee_rate=self.config.taker_fee)
                rt = self._rt(bot.id)
                if out.status == "DEFERRED":
                    key = (p.id, out.reason or "")
                    if key not in rt.deferred_logged:
                        rt.deferred_logged.add(key)
                        log.warning("exit deferred %s %s: %s (retrying every tick)",
                                    bot.name, symbol, out.reason)
                    await db.commit()
                    continue
                rt.counters.exits += 1
                rt.last_snapshot = None  # snapshot after a fill
                rt.last_reconcile = None
                log.info("exit %s %s: %s (%s) sold=%s pnl_net=%s R=%s", bot.name, symbol,
                         out.status, reason, _g(out.sold_qty), _q(out.realized_pnl_net),
                         _g(out.r_multiple, 3))
                await db.commit()

    # ------------------------------------------------------------------ #
    # 3. entries
    # ------------------------------------------------------------------ #
    async def _volume_24h(self, db: AsyncSession) -> dict[str, Decimal | None]:
        rows = await db.execute(select(Instrument.id, Instrument.volume_24h_usd, Instrument.in_universe))
        return {iid: vol for iid, vol, inu in rows if inu}

    async def step_entries(self) -> None:
        async with self._sessions() as db:
            bots = (await db.scalars(select(Bot).where(Bot.status == BotStatus.RUNNING))).all()
            bot_ids = [b.id for b in bots]
            universe = await self._volume_24h(db)
        for bot_id in bot_ids:
            for iid, vol in sorted(universe.items()):
                await self._scan_instrument(bot_id, iid, vol)
            await self._maybe_emit_cycle(bot_id, [iid.split(":", 1)[1] for iid in universe])

    async def _maybe_emit_cycle(self, bot_id: uuid.UUID, symbols: list[str]) -> None:
        """SCAN_CYCLE_COMPLETED once every universe pair was evaluated for the window."""
        rt = self._rt(bot_id)
        window_end = self._now().replace(second=0, microsecond=0)
        if not symbols or window_end in rt.emitted_cycles:
            return
        if any(rt.last_window.get(s) != window_end for s in symbols):
            return
        c = rt.cycles.pop(window_end, _Counters())
        rt.emitted_cycles = {w for w in rt.emitted_cycles if w > window_end - MINUTE * 10}
        rt.emitted_cycles.add(window_end)
        for w in [w for w in rt.cycles if w < window_end]:
            del rt.cycles[w]
        async with self._sessions() as db:
            await self._emit(db, "SCAN_CYCLE_COMPLETED", "bot", bot_id, {
                "window_end": window_end, "evaluated": c.evaluated, "unknown": c.unknown,
                "candidates": c.candidates, "rejected": c.rejected,
                "fails": dict(c.fails), "unknown_reasons": dict(c.unknown_reasons)},
                bot_id=bot_id, corr=None)
            await db.commit()

    async def _scan_instrument(self, bot_id: uuid.UUID, iid: str, vol_24h: Decimal | None) -> None:
        symbol = iid.split(":", 1)[1]
        now = self._now()
        window_end = now.replace(second=0, microsecond=0)
        rt = self._rt(bot_id)
        if rt.last_window.get(symbol) == window_end:
            return
        fresh = self.market.is_fresh(symbol)
        book = self.market.book(symbol)
        async with self._sessions() as db:
            bot = await db.get(Bot, bot_id)
            if bot.status is not BotStatus.RUNNING:
                return
            session = await cmds.active_session(db, bot.id)
            if session is None:
                return
            strat = await self._strategy(db, bot.strategy_version_id)
            policy = await self._policy(db, bot.risk_policy_version_id)
            minutes = await self._load_minutes(db, iid, start=window_end - MINUTE * 70, end=window_end)
            feats = window_features(minutes, window_end)
            feats["spread_bps"] = spread_feature(book, fresh)
            incomplete = feats["volume_spike"].reason == "CURRENT_WINDOW_INCOMPLETE"
            settled = (now - window_end).total_seconds() >= self.config.window_settle_seconds
            if incomplete and fresh and not settled:
                return  # minute rows not flushed yet: retry this window on a later tick
            rt.last_window[symbol] = window_end
            rt.count(window_end, "evaluated")
            if not fresh:
                rt.count(window_end, "unknown")
                rt.count_map(window_end, "unknown_reasons", ["FEED_NOT_LIVE"])
                return
            verdict, details = evaluate_conditions(strat.entry.model_dump(), feats)
            if verdict == "FAIL":
                rt.count_map(window_end, "fails",
                             [d["feature"] for d in details if d["result"] == "FAIL"])
                return
            if verdict == "UNKNOWN":
                rt.count(window_end, "unknown")
                rt.count_map(window_end, "unknown_reasons", [
                    f"{d['feature']}:{feats[d['feature']].reason if d['feature'] in feats else 'MISSING'}"
                    for d in details if d["result"] == "UNKNOWN"])
                return
            # exclusions: position/intent in progress on this pair
            busy = await db.scalar(select(Position.id).where(
                Position.bot_id == bot.id, Position.instrument_id == iid,
                Position.status.in_((PositionStatus.OPEN, PositionStatus.REDUCING,
                                     PositionStatus.PENDING))).limit(1))
            if busy is not None:
                return
            candidate = await self._create_candidate(db, bot, session.id, iid, strat, window_end,
                                                     feats, details)
            if candidate is None:
                return
            rt.count(window_end, "candidates")
            sizing = await self._assess_and_size(db, bot, candidate, iid, symbol, feats, vol_24h,
                                                 strat, policy, fresh, book, now)
            await db.commit()
        if sizing is None or not sizing.approved:
            rt.count(window_end, "rejected")
            return
        await self._sleep(self.config.latency_seconds)
        async with self._sessions() as db:
            candidate = await db.get(Candidate, candidate.id)
            sizing = await db.get(SizingDecision, sizing.id)
            out = await execute_entry(
                db, sizing=sizing, candidate=candidate, book=self.market.book(symbol),
                book_fresh=self.market.is_fresh(symbol),
                max_slippage_pct=policy.dec("max_slippage_pct") or ZERO,
                initial_stop_pct=Decimal(strat.exit.initial_stop_pct),
                take_profit_r=Decimal(strat.exit.take_profit_r) if strat.exit.take_profit_r else None,
                now=self._now(), fee_rate=self.config.taker_fee)
            await db.commit()
        if out.position is not None:
            rt.counters.entries += 1
            rt.last_snapshot = None
            rt.last_reconcile = None
            log.info("entry %s %s: %s qty=%s avg=%s stop=%s tp=%s decision_to_fill=%sms", bot.name,
                     symbol, out.status, _g(out.position.quantity), _g(out.position.avg_entry_price),
                     _g(out.position.initial_stop_price), _g(out.position.take_profit_price),
                     out.latency_ms)
        else:
            log.info("entry %s %s not executed: %s", bot.name, symbol, out.reason)

    async def _create_candidate(self, db, bot, session_id, iid, strat, window_end, feats, details):
        exists = await db.scalar(select(Candidate.id).where(
            Candidate.session_id == session_id, Candidate.instrument_id == iid,
            Candidate.strategy_version_id == bot.strategy_version_id,
            Candidate.window_start == window_end - MINUTE * 5).limit(1))
        if exists is not None:
            return None
        cand = Candidate(bot_id=bot.id, session_id=session_id, instrument_id=iid,
                         strategy_version_id=bot.strategy_version_id,
                         window_start=window_end - MINUTE * 5, status=CandidateStatus.ENRICHING,
                         features={k: f.as_json() for k, f in feats.items()} | {"conditions": details})
        db.add(cand)
        await db.flush()
        await self._emit(db, "CANDIDATE_DETECTED", "candidate", cand.id,
                         {"instrument_id": iid, "window_start": cand.window_start,
                          "features": {k: f.as_json()["value"] for k, f in feats.items()}},
                         bot_id=bot.id, corr=cand.id)
        log.info("candidate %s %s: spike=%s buy_share=%s change=%s spread_bps=%s notional=%s "
                 "trades=%s", bot.name, iid.split(":", 1)[1], *(_short(feats[k]) for k in
                 ("volume_spike", "buy_notional_share", "price_change_5m", "spread_bps",
                  "window_notional_usd", "window_trade_count")))
        return cand

    async def _last_exit_at(self, db, bot_id, iid) -> datetime | None:
        return await db.scalar(select(Position.closed_at).where(
            Position.bot_id == bot_id, Position.instrument_id == iid,
            Position.status == PositionStatus.CLOSED).order_by(Position.closed_at.desc()).limit(1))

    async def _assess_and_size(self, db, bot, cand, iid, symbol, feats, vol_24h, strat, policy,
                               fresh, book, now) -> SizingDecision | None:
        cooldown = strat.cooldown_seconds if strat.cooldown_seconds is not None else (policy.cooldown_seconds or 0)
        verdict, criteria = evaluate_market_gate(
            rules=self.market.rules.get(symbol), fresh=fresh, book=book, policy=policy,
            price_change_5m=feats["price_change_5m"].value, volume_24h_usd=vol_24h,
            last_exit_at=await self._last_exit_at(db, bot.id, iid), cooldown_seconds=cooldown,
            now=now)
        db.add(RiskAssessment(candidate_id=cand.id, verdict=AssessmentVerdict(verdict),
                              criteria={c.name: c.as_json(now) for c in criteria},
                              risk_policy_version_id=bot.risk_policy_version_id))
        failed = [c.name for c in criteria if c.result != "PASS"]
        if verdict != "PASS":
            cand.status = CandidateStatus.REJECTED
            await self._emit(db, "RISK_REJECTED", "candidate", cand.id,
                             {"stage": "RUG_CHECK", "failed": failed, "instrument_id": iid}, bot_id=bot.id, corr=cand.id)
            log.info("rug check %s %s: REJECT %s", bot.name, symbol, failed)
            return None
        await self._emit(db, "RISK_PASSED", "candidate", cand.id, {"stage": "RUG_CHECK", "instrument_id": iid},
                         bot_id=bot.id, corr=cand.id)

        view = await compute_equity(db, bot.id, self.market)
        rules = self.market.rules[symbol]
        inputs = SizingInputs(
            equity=view.equity, cash_free=view.cash, open_positions=len(view.open_positions),
            open_exposure_cost=sum((p.cost_basis for p in view.open_positions), ZERO),
            asks=book.top_asks(book.depth), bids=book.top_bids(book.depth),
            qty_precision=rules.qty_precision, qty_min=rules.qty_min, cost_min=rules.cost_min,
            policy=policy, initial_stop_pct=Decimal(strat.exit.initial_stop_pct),
            taker_fee=self.config.taker_fee, daily_loss=await daily_loss(db, bot.id, view.equity, now),
            drawdown=await drawdown(db, bot.id, view))
        res = size_position(inputs)
        input_hash = sha256_hex({"equity": view.equity, "cash": view.cash, "ask": inputs.asks[:3],
                                 "bid": inputs.bids[:3], "policy": policy.model_dump(mode="json")})
        sizing = SizingDecision(
            candidate_id=cand.id, approved=res.approved, notional_requested=res.notional_requested,
            notional_approved=res.notional_approved, quantity=res.quantity,
            reference_price=res.reference_price, stop_price=res.stop_price,
            risk_budget=res.risk_budget, limits_applied=res.limits_applied,
            rejection_reasons=res.rejection_reasons, input_hash=input_hash,
            expires_at=now + timedelta(seconds=policy.authorization_ttl_seconds or 10))
        db.add(sizing)
        await db.flush()
        if res.approved:
            await self._emit(db, "POSITION_SIZED", "candidate", cand.id,
                             {"instrument_id": iid, "quantity": res.quantity, "notional": res.notional_approved,
                              "binding_limit": res.limits_applied.get("binding"),
                              "risk_budget": res.risk_budget}, bot_id=bot.id, corr=cand.id)
            log.info("sizing %s %s: qty=%s notional=%s (binding %s)", bot.name, symbol, _g(res.quantity),
                     _q(res.notional_approved), res.limits_applied.get("binding"))
        else:
            cand.status = CandidateStatus.REJECTED
            await self._emit(db, "RISK_REJECTED", "candidate", cand.id,
                             {"stage": "SIZER", "reasons": res.rejection_reasons, "instrument_id": iid},
                             bot_id=bot.id, corr=cand.id)
            log.warning("sizing %s %s refused: %s", bot.name, symbol, res.rejection_reasons)
        return sizing

    # ------------------------------------------------------------------ #
    # 4. equity, snapshots, reconciliation, STOPPING → STOPPED
    # ------------------------------------------------------------------ #
    async def step_equity(self) -> None:
        async with self._sessions() as db:
            bots = (await db.scalars(select(Bot).where(Bot.status.in_((
                BotStatus.RUNNING, BotStatus.PAUSED, BotStatus.STOPPING, BotStatus.DEGRADED))))).all()
            bot_ids = [b.id for b in bots]
        for bot_id in bot_ids:
            async with self._sessions() as db:
                bot = await db.get(Bot, bot_id)
                await self._equity_for(db, bot)
                await db.commit()

    async def _equity_for(self, db: AsyncSession, bot: Bot) -> None:
        now = self._now()
        rt = self._rt(bot.id)
        if rt.last_reconcile is None or (now - rt.last_reconcile).total_seconds() >= self.config.reconcile_seconds:
            rt.last_reconcile = now
            result = await reconcile(db, bot.id)
            if result.ok:
                await self._emit(db, "RECONCILIATION_OK", "bot", bot.id,
                                 {"expired_intents": result.expired_intents}, bot_id=bot.id, corr=None)
            else:
                await self._emit(db, "RECONCILIATION_FAILED", "bot", bot.id, {"issues": result.issues},
                                 bot_id=bot.id, corr=None)
                log.warning("reconciliation FAILED for %s: %s", bot.name, result.issues)
                if bot.status in (BotStatus.RUNNING, BotStatus.PAUSED):
                    await transition_bot(db, bot, BotStatus.DEGRADED, actor=WORKER_ACTOR,
                                         reason="RECONCILIATION_FAILED")
        view = await compute_equity(db, bot.id, self.market)
        rt.estimated = view.estimated
        for p in view.open_positions:  # marks for the API/terminal (10-backend/04)
            symbol = p.instrument_id.split(":", 1)[1]
            value = liquidation_value(p.quantity, self.market, p.instrument_id)
            book = self.market.book(symbol)
            p.mark_value = value
            if value is not None:
                p.mark_price = book.best_bid[0] if book and book.best_bid else None
                p.marked_at = now
        if rt.last_snapshot is None or (now - rt.last_snapshot).total_seconds() >= self.config.snapshot_seconds:
            rt.last_snapshot = now
            realized = await realized_pnl_net(db, bot.id)
            await write_snapshot(db, bot.id, view, now, realized)
            await self._emit(db, "EQUITY_UPDATED", "bot", bot.id,
                             {"equity": view.equity, "cash": view.cash, "positions_value": view.positions_value,
                              "positions_value_estimated": view.estimated,
                              "realized_pnl_net": realized, "unrealized_pnl": view.unrealized,
                              "quality": view.quality.value}, bot_id=bot.id, corr=None)
        if bot.status is BotStatus.STOPPING and not view.open_positions:
            pending = await db.scalar(select(OrderIntent.id).where(
                OrderIntent.bot_id == bot.id, OrderIntent.status == "CREATED").limit(1))
            if pending is None:
                await transition_bot(db, bot, BotStatus.STOPPED, actor=WORKER_ACTOR,
                                     reason="no open position left")
                session = await cmds.active_session(db, bot.id)
                if session:
                    session.status, session.ended_at = SessionStatus.STOPPED, now
                log.info("bot %s STOPPED (session closed)", bot.name)

    # ------------------------------------------------------------------ #
    def _maybe_summary(self) -> None:
        now = self._now()
        if self._last_summary is None:
            self._last_summary = now
            return
        if (now - self._last_summary).total_seconds() < self.config.summary_seconds:
            return
        self._last_summary = now
        for bot_id, rt in self._runtime.items():
            c = rt.counters
            fails = " ".join(f"{k}={v}" for k, v in c.fails.most_common()) or "-"
            log.info("agents summary bot=%s: evaluated=%d unknown=%d candidates=%d rejected=%d "
                     "entries=%d exits=%d positions_beyond_book=%s fails: %s", str(bot_id)[:8],
                     c.evaluated, c.unknown, c.candidates, c.rejected, c.entries, c.exits,
                     _q(rt.estimated), fails)
            rt.counters = _Counters()


def _short(f: Feature) -> str:
    return "?" if f.value is None else format(f.value, ".6g")


def _q(v: Decimal | None) -> str:
    return "?" if v is None else f"{v:.2f}"


def _g(v: Decimal | None, digits: int = 10) -> str:
    """Readable log value (at most `digits` significant digits); stored values stay exact."""
    return "?" if v is None else format(v.normalize(), f".{digits}g")

