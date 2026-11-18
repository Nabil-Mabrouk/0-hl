"""T-M1.2-13 — worker logging: idempotent stdout handler, mandatory INFO/WARNING lines."""

import asyncio
import io
import logging

import pytest

from app.domain.trading.logging_setup import DOMAIN_LOGGER, configure_logging
from app.domain.trading.market.service import MarketDataConfig, MarketDataService
from app.domain.trading.seed import run_seed

from .market_fakes import FakeConnector, FakeTransport, rest_client, wait_until, ws_session_lines


@pytest.fixture(autouse=True)
def _enable_hl_loggers():
    """alembic's fileConfig() (run by the migration test) disables existing loggers."""
    for name, obj in logging.root.manager.loggerDict.items():
        if (name == DOMAIN_LOGGER or name.startswith(DOMAIN_LOGGER + ".")) and isinstance(obj, logging.Logger):
            obj.disabled = False


@pytest.fixture
def restore_hl_logger():
    logger = logging.getLogger(DOMAIN_LOGGER)
    handlers, propagate, level = list(logger.handlers), logger.propagate, logger.level
    yield logger
    logger.handlers[:] = handlers
    logger.propagate, logger.level = propagate, level


def test_configure_logging_is_idempotent_and_writes_utc_lines(restore_hl_logger):
    buf = io.StringIO()
    logger = configure_logging(stream=buf)
    configure_logging(stream=buf)
    marked = [h for h in logger.handlers if getattr(h, "_hl_stdout_handler", False)]
    assert len(marked) == 1 and logger.propagate is False
    logging.getLogger("hl.market").info("hello %s", "world")
    line = buf.getvalue().strip()
    assert line.endswith("| INFO | hl.market | hello world")
    assert line.split(" | ")[0].endswith("Z")


@pytest.mark.anyio
async def test_mandatory_lines_are_logged(session_factory, caplog):
    lines = ws_session_lines()
    first = FakeTransport(lines[:80], then="hang")
    second = FakeTransport(lines, then="hang")
    async with session_factory() as db:
        await run_seed(db)
    cfg = MarketDataConfig(watchdog_seconds=0.3, housekeeping_seconds=0.05, summary_seconds=0.1)
    svc = MarketDataService(session_factory, rest_client(), FakeConnector([first, second]), config=cfg)
    svc.ws.backoff_initial = 0.05
    with caplog.at_level(logging.INFO, logger=DOMAIN_LOGGER):
        await svc.refresh_universe()
        stop = asyncio.Event()
        task = asyncio.create_task(svc.run(stop))
        await wait_until(lambda: svc.ws.connections == 2 and svc.state.is_fresh("BTC/USD"))
        await wait_until(lambda: second.queue.empty())
        second.push({"channel": "book", "type": "update", "data": [{
            "symbol": "BTC/USD", "bids": [{"price": 1.0, "qty": 1.0}], "asks": [],
            "checksum": 1, "timestamp": "2026-11-18T06:15:00.000000Z"}]})
        await wait_until(lambda: "feed summary" in caplog.text and "book resync" in caplog.text)
        stop.set()
        await task
    text = caplog.text
    for expected in ("universe:", "backfill done:", "kraken ws connected (connection #1",
                     "kraken ws disconnected: reason=WATCHDOG_TIMEOUT",
                     "book resync BTC/USD: reason=CHECKSUM_MISMATCH", "feed summary:",
                     "market data stopped"):
        assert expected in text, expected
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("disconnected" in r.getMessage() for r in warnings)
    assert any("book resync" in r.getMessage() for r in warnings)
