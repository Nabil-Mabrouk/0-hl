"""STRAT-06 and STRAT-11 offline studies (09-recherche/06 and /07: T-R22, T-R23, T-R32, T-R33)."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.trading.agents.features import BTC_IID, MinuteData
from app.domain.trading.research.strat01_study import Bar, PairData
from app.domain.trading.research.strat02_study import Row, verdict
from app.domain.trading.research import strat06_study as s06
from app.domain.trading.research import strat11_study as s11

D = Decimal
T0 = datetime(2026, 11, 18, 0, 0, tzinfo=timezone.utc)
M = timedelta(minutes=1)
IID = "kraken_spot:TEST/USD"


def _pair(closes: list[Decimal]) -> PairData:
    pd = PairData()
    for i, c in enumerate(closes):
        pd.minutes[T0 + M * i] = MinuteData(D(30000), D(15000), c, "live", 20, c, c)
        pd.bars[T0 + M * i] = Bar(c, c, c, c)
    return pd


# T-R22 -------------------------------------------------------------------------------------
def test_back_to_the_mean_exit_uses_only_past_minutes_and_stop_wins():
    closes = [D(100)] * 300 + [D(95)] * 5 + [D(97), D(99), D(101), D(101)] + [D(101)] * 50
    series = {IID: _pair(closes)}
    gap = s06.GapCache(series)
    at = T0 + M * 305                                         # entry on the rebound
    rule = s06.ExitRule(120, D(10), None)
    tr = s06.simulate_exit(IID, series[IID].bars, at, D(95), rule, gap)
    assert tr.reason == "EXIT_CONDITION"
    # the gap reached ≥ 0 once a minute closed at 101 (index 307): exit at that close, decided at 308
    assert tr.closed_at == T0 + M * 308 and tr.exit == D(101)
    assert gap(IID, T0 + M * 308) >= 0 and gap(IID, T0 + M * 307) < 0

    crash = [D(100)] * 300 + [D(95)] * 5 + [D(80)] * 30
    s2 = {IID: _pair(crash)}
    tr = s06.simulate_exit(IID, s2[IID].bars, at, D(95), s06.ExitRule(120, D(4), None), s06.GapCache(s2))
    assert tr.reason == "STOP"


# T-R23 -------------------------------------------------------------------------------------
def test_btc_filter_blocks_a_market_wide_drop():
    sig = s06.Signal(T0, D(95), D(3), D(30000), D("0.001"), D("-0.05"), D("-0.02"), D("-0.03"))
    assert not s06.EntryRule(D("0.04"), D(2), True).accepts(sig)
    assert s06.EntryRule(D("0.04"), D(2), False).accepts(sig)
    ok_btc = s06.Signal(T0, D(95), D(3), D(30000), D("0.001"), D("-0.05"), D("-0.02"), D("-0.005"))
    assert s06.EntryRule(D("0.04"), D(2), True).accepts(ok_btc)
    falling = s06.Signal(T0, D(95), D(3), D(30000), D("-0.001"), D("-0.05"), D("-0.02"), D("-0.005"))
    assert not s06.EntryRule(D("0.04"), D(2), True).accepts(falling)          # not turning up yet
    dead = s06.Signal(T0, D(95), D(3), D(30000), D("0.001"), D("-0.05"), None, D("-0.005"))
    assert not s06.EntryRule(D("0.04"), D(2), True).accepts(dead)             # unknown 24 h trend


# T-R32 -------------------------------------------------------------------------------------
def _universe(n_minutes: int, strong: dict[str, Decimal]) -> dict[str, PairData]:
    """22 pairs (BTC included); `strong` pairs drift up by the given step per minute."""
    out: dict[str, PairData] = {}
    names = [BTC_IID] + [f"kraken_spot:P{i:02d}/USD" for i in range(21)]
    for iid in names:
        step = strong.get(iid, D(0))
        out[iid] = _pair([D(100) + step * i for i in range(n_minutes)])
    return out


def test_portfolio_never_exceeds_its_slots_and_takes_by_rank():
    strong = {"kraken_spot:P00/USD": D("0.03"), "kraken_spot:P01/USD": D("0.02"),
              "kraken_spot:P02/USD": D("0.01")}
    n = 1440 + 200
    series = _universe(n, strong)
    start, end = T0 + M * 1441, T0 + M * (n - 1)
    market = s11.market_series(series, start, end)
    snap = market[start].pairs["kraken_spot:P00/USD"]
    assert snap.rank == 1 and snap.rs is not None and snap.rs > 0
    trades = s11.run_portfolio(series, market, start, end, s11.EntryRule(2, False), s11.ExitRule(60, D(10), 5))
    assert {t.iid for t in trades} <= {"kraken_spot:P00/USD", "kraken_spot:P01/USD"}
    # at most 2 open at once
    events = sorted([(t.at, 1) for t in trades] + [(t.closed_at, -1) for t in trades], key=lambda e: (e[0], e[1]))
    open_now = peak = 0
    for _, d in events:
        open_now += d
        peak = max(peak, open_now)
    assert peak <= 2 and trades and all(t.reason == "TIME" for t in trades)


# T-R33 -------------------------------------------------------------------------------------
def _trades(k: int, pct: str):
    from app.domain.trading.research.strat01_study import Trade
    px = D(100) * (1 + D(pct) / 100)
    return [Trade(IID, T0 + M * i, D(100), px, "TIME", T0 + M * (i + 60)) for i in range(k)]


def test_strat11_verdict_threshold_and_not_judgeable():
    ex = s11.ExitRule(2880, D(10), 6)
    good = Row(s11.EntryRule(3, True), ex, _trades(12, "4"), _trades(16, "4"))  # type: ignore[arg-type]
    base = Row(None, ex, _trades(30, "0"), _trades(30, "0"))  # type: ignore[arg-type]
    assert verdict(good, base, min_trades=s11.MIN_TRADES)["launch"] is True
    v = verdict(good, base, min_trades=s11.MIN_TRADES, not_judgeable="10.0 jours de données, 28 requis")
    assert v["launch"] is False and v["judgeable"] is False and v["criteria"]
    assert verdict(good, base)["launch"] is False                             # 16 < 20 for STRAT-02/06
