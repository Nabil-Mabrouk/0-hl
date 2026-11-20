"""M2.5 — trades and statistics (03-ecrans/M2-trades-et-statistiques, T-M2.5-01..06)."""

import csv
import io
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.domain.trading.analytics import TradeRow, compute_stats, performance_curve, stats_report
from app.domain.trading.enums import CashMovementKind, PositionStatus, SnapshotQuality
from app.domain.trading.ledger import record_cash_movement
from app.domain.trading.models import Bot, EquitySnapshot, Fill, Order, OrderIntent, Position

from .agent_fakes import NOW
from .test_api import _h, world  # noqa: F401  (fixture)

pytestmark = pytest.mark.anyio
D = Decimal


def _row(net, fees="1", reason="TIME", iid="kraken_spot:A/USD", version=("v1", "0.2.0"), notional="1000"):
    p = Position(instrument_id=iid, realized_pnl_net=None if net is None else D(net), fees_paid=D(fees),
                 exit_reason=reason, r0_usd=D(10), opened_at=NOW, closed_at=NOW + timedelta(minutes=30),
                 avg_entry_price=D(100), status=PositionStatus.CLOSED)
    return TradeRow(p, version[0], "volume-spike", version[1], D(notional), D(notional), D(10), D(100), None)


# T-M2.5-01 / 02 ----------------------------------------------------------------------------
async def test_trades_carry_their_session_version_and_fill_figures(world, session_factory):  # noqa: F811
    await world.trade_cycle()                                         # one TP trade under v0.2.0
    obs = _h(world.observer.id)
    r = await world.client.get(f"/api/v1/trades?bot_id={world.bot_id}", headers=obs)
    assert r.status_code == 200 and r.json()["total"] == 1
    t = r.json()["items"][0]
    async with session_factory() as db:
        fills = (await db.execute(select(OrderIntent.purpose, Fill.qty, Fill.price)
                                  .join(Order, Order.intent_id == OrderIntent.id)
                                  .join(Fill, Fill.order_id == Order.id))).all()
    entry = sum(q * p for purpose, q, p in fills if purpose.value == "ENTRY")
    assert D(t["entry_notional"]) == entry
    assert D(t["net_pct"]) == D(t["realized_pnl_net"]) / entry * 100
    assert t["strategy"]["version"] == "0.2.0" and t["exit_reason"] == "TAKE_PROFIT"
    assert t["hold_seconds"] == 10 and t["entry_slippage_bps"] is not None
    assert t["correlation_id"] is not None

    # the bot is re-attached to another version: the old trade keeps 0.2.0 (T-M2.3 binding)
    from app.domain.trading import lifecycle as lc
    from app.domain.trading.models import RiskPolicyVersion, StrategyVersion
    async with session_factory() as db:
        bot = await db.get(Bot, world.bot_id)
        sv = await db.get(StrategyVersion, bot.strategy_version_id)
        v3 = await lc.duplicate_strategy_version(db, sv, version="0.3.0", actor="user:1")
        await lc.publish_strategy(db, v3, actor="user:1", reason="t")
        bot.status = __import__("app.domain.trading.enums", fromlist=["BotStatus"]).BotStatus.STOPPED
        await db.flush()
        await lc.bind_bot(db, bot, strategy_version=v3,
                          risk_policy_version=await db.get(RiskPolicyVersion, bot.risk_policy_version_id),
                          actor="user:1", reason="t")
        await db.commit()
    again = (await world.client.get(f"/api/v1/trades?bot_id={world.bot_id}", headers=obs)).json()["items"][0]
    assert again["strategy"]["version"] == "0.2.0"

    assert (await world.client.get("/api/v1/trades", headers=obs)).json()["total"] == 1      # all bots
    assert (await world.client.get("/api/v1/trades?exit_reason=STOP", headers=obs)).json()["total"] == 0
    assert (await world.client.get("/api/v1/trades?instrument_id=kraken_spot:TEST/USD",
                                   headers=obs)).json()["total"] == 1
    page = (await world.client.get("/api/v1/trades?limit=1&offset=1", headers=obs)).json()
    assert page["items"] == [] and page["total"] == 1


# T-M2.5-03 ---------------------------------------------------------------------------------
def test_statistics_formulas_and_groupings():
    rows = [_row("30", fees="2"), _row("-10", reason="STOP"), _row("-10"), _row("0"),
            _row("20", version=("v2", "0.3.0"), iid="kraken_spot:B/USD")]
    s = compute_stats(rows)
    assert (s["trades"], s["wins"], s["losses"], s["breakeven"]) == (5, 2, 2, 1)
    assert s["net_usd"] == D(30) and s["fees_usd"] == D(6) and s["gross_usd"] == D(36)
    assert s["fees_share_of_gross_pct"] == D(6) / D(36) * 100
    assert s["profit_factor"] == D(50) / D(20) and s["expectancy_usd"] == D(6)
    assert s["avg_win_usd"] == D(25) and s["avg_loss_usd"] == D(-10)
    assert s["exits"] == {"STOP": 1, "TIME": 4} and s["sample_too_small"] is True
    assert s["best_pair"]["instrument_id"] == "kraken_spot:B/USD"          # B +20, A +30−10−10+0 = +10
    assert s["worst_pair"]["instrument_id"] == "kraken_spot:A/USD"
    assert compute_stats([_row("5")])["profit_factor"] is None           # no loss ⇒ unknown, not 0
    assert compute_stats([])["win_rate_pct"] is None
    rep = stats_report(rows)
    assert [(v["version"], v["trades"]) for v in rep["by_version"]] == [("0.2.0", 4), ("0.3.0", 1)]
    assert [p["instrument_id"] for p in rep["by_pair"]] == ["kraken_spot:A/USD", "kraken_spot:B/USD"]
    assert compute_stats([_row("1")] * 20)["sample_too_small"] is False


# T-M2.5-04 ---------------------------------------------------------------------------------
async def test_curve_excludes_deposits_and_stale_snapshots(world, session_factory):  # noqa: F811
    async with session_factory() as db:
        bot_id = world.bot_id
        t0 = NOW - timedelta(hours=1)
        from app.domain.trading.models import CashMovement
        for cm in (await db.scalars(select(CashMovement))).all():
            cm.occurred_at = t0 - timedelta(minutes=1)                 # seed deposit: 10 000 before t0

        def snap(minute, equity, quality=SnapshotQuality.OK):
            db.add(EquitySnapshot(bot_id=bot_id, at=t0 + timedelta(minutes=minute), cash=D(equity),
                                  cash_reserved=D(0), positions_mark_value=D(0), equity=D(equity),
                                  realized_pnl_net=D(0), unrealized_pnl=D(0), quality=quality))
        snap(0, "10000")
        snap(1, "9900")                                                # −100 of performance
        snap(2, "1", SnapshotQuality.STALE)                            # ignored
        dep = await record_cash_movement(db, bot_id=bot_id, kind=CashMovementKind.DEPOSIT, amount=D(1000),
                                         actor="user:1", reason="apport")
        dep.occurred_at = t0 + timedelta(minutes=3)
        snap(4, "10900")                                               # deposit, not a gain
        snap(5, "11000")
        await db.commit()
    r = await world.client.get(f"/api/v1/stats/curve?bot_id={bot_id}", headers=_h(world.observer.id))
    body = r.json()
    perf = [D(p["performance"]) for p in body["points"]]
    assert perf == [D(0), D(-100), D(-100), D(0)]
    assert D(body["max_drawdown_pct"]) == D(100) / D(10000) * 100
    async with session_factory() as db:
        small = await performance_curve(db, bot_id, max_points=2)
    assert len(small["points"]) <= 4 and small["points"][-1]["performance"] == D(0)
    assert any(p["performance"] == D(-100) for p in small["points"])     # trough always kept


# T-M2.5-05 / 06 ----------------------------------------------------------------------------
async def test_csv_matches_api_and_open_positions_are_excluded(world, session_factory):  # noqa: F811
    obs = _h(world.observer.id)
    await world.engine.tick()                                          # entry only: position OPEN
    st = (await world.client.get(f"/api/v1/stats?bot_id={world.bot_id}", headers=obs)).json()
    assert st["overall"]["trades"] == 0 and st["overall"]["profit_factor"] is None
    assert st["overall"]["net_usd"] == "0" and st["overall"]["win_rate_pct"] is None
    from .agent_fakes import set_book
    set_book(world.state, asks=((108.2, 50),), bids=((108, 50),))
    world.clock.advance(seconds=10)
    await world.engine.tick()                                          # TP exit
    api = (await world.client.get(f"/api/v1/trades?bot_id={world.bot_id}", headers=obs)).json()["items"]
    resp = await world.client.get(f"/api/v1/trades.csv?bot_id={world.bot_id}", headers=obs)
    assert resp.headers["content-type"].startswith("text/csv")
    lines = list(csv.DictReader(io.StringIO(resp.text), delimiter=";"))
    assert len(lines) == len(api) == 1
    assert lines[0]["realized_pnl_net"] == api[0]["realized_pnl_net"]
    assert lines[0]["position_id"] == api[0]["id"]
    st = (await world.client.get(f"/api/v1/stats?bot_id={world.bot_id}", headers=obs)).json()
    assert st["overall"]["trades"] == 1 and st["by_version"][0]["version"] == "0.2.0"
    assert uuid.UUID(st["by_version"][0]["version_id"])
