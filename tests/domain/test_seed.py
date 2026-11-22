"""T-M1.1-12 — idempotent seed; seed data must equal the spec examples (ADR-000)."""

import json
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.domain.trading.enums import BotStatus, Environment, StrategyVersionStatus
from app.domain.trading.ledger import equity_summary
from app.domain.trading.models import (
    AuditEvent,
    Bot,
    CashMovement,
    DomainEvent,
    LedgerEntry,
    RiskPolicyVersion,
    StrategyVersion,
)
from app.domain.trading.seed import (
    OBSERVATION_BOTS,
    OBSERVATION_POLICIES,
    POLICY_FILE,
    SEED_DIR,
    STRATEGY_FILE,
    run_seed,
)

from .factories import SPEC_ROOT

pytestmark = pytest.mark.anyio

TABLES = (StrategyVersion, RiskPolicyVersion, Bot, CashMovement, LedgerEntry, AuditEvent, DomainEvent)


async def _counts(db) -> dict:
    return {t.__tablename__: await db.scalar(select(func.count()).select_from(t)) for t in TABLES}


async def test_seed_creates_expected_state(db):
    result = await run_seed(db)
    assert result.bot.environment is Environment.PAPER
    assert result.bot.status is BotStatus.READY
    assert result.strategy_version.status is StrategyVersionStatus.PUBLISHED
    summary = await equity_summary(db, result.bot.id, positions_mark_value=None)
    assert summary.equity == Decimal("10000")
    assert summary.realized_pnl_gross == Decimal(0)
    published = await db.scalar(
        select(AuditEvent).where(AuditEvent.action == "STRATEGY_PUBLISHED")
    )
    assert published.self_approved is True and published.actor == "system:seed"


async def test_seed_is_idempotent(session_factory):
    async with session_factory() as db:
        await run_seed(db)
        first = await _counts(db)
    async with session_factory() as db:
        await run_seed(db)
        second = await _counts(db)
    assert first == second


@pytest.mark.parametrize(
    "seed_file,spec_file",
    [
        (STRATEGY_FILE, "examples/strategy-volume-spike.json"),
        (POLICY_FILE, "examples/risk-policy-paper-default.json"),
        *[(SEED_DIR / f, f"examples/{f}") for f, *_ in (*OBSERVATION_POLICIES, *OBSERVATION_BOTS)],
    ],
)
def test_seed_data_matches_spec_examples(seed_file, spec_file):
    seed = json.loads(seed_file.read_text(encoding="utf-8"))
    spec = json.loads((SPEC_ROOT / spec_file).read_text(encoding="utf-8"))
    assert seed == spec, f"{seed_file.name} drifted from the spec ({spec_file})"


async def test_seed_rebinds_never_started_bot_only(session_factory):
    """ADR-014: a seed bot without session follows the current seed strategy version."""
    import copy
    from app.domain.trading.bots import transition_bot
    from app.domain.trading.enums import BotStatus as BS, Environment as Env, SessionStatus
    from app.domain.trading.models import BotSession
    from app.domain.trading.versioning import create_strategy_version, publish_strategy_version
    from app.domain.trading.seed import SEED_BOT_NAME, _ensure_policy
    from app.domain.trading.bots import create_bot

    old_cfg = json.loads(STRATEGY_FILE.read_text(encoding="utf-8"))
    old_cfg = copy.deepcopy(old_cfg) | {"version": "0.1.0"}
    old_cfg["entry"]["conditions"] = old_cfg["entry"]["conditions"][2:]
    async with session_factory() as db:   # state of production before ADR-014
        pv = await _ensure_policy(db)
        v1 = await create_strategy_version(db, config=old_cfg, name="vs", actor="system:seed")
        await publish_strategy_version(db, v1, actor="system:seed", reason="t", self_approved=True)
        bot = await create_bot(db, name=SEED_BOT_NAME, environment=Env.PAPER, strategy_version=v1,
                               risk_policy_version=pv, actor="system:seed")
        await transition_bot(db, bot, BS.READY, actor="system:seed", reason="t")
        await db.commit()
    async with session_factory() as db:
        result = await run_seed(db)
        assert result.bot.strategy_version_id == result.strategy_version.id
        assert result.strategy_version.version == "0.2.0"
        rebind = await db.scalar(select(AuditEvent).where(AuditEvent.action == "SEED_REBIND"))
        assert rebind is not None
        # once the bot has had a session, the seed never touches it again
        db.add(BotSession(bot_id=result.bot.id, environment=Env.PAPER,
                          strategy_version_id=result.strategy_version.id, strategy_sha256="0" * 64,
                          risk_policy_version_id=result.policy_version.id,
                          risk_policy_sha256="0" * 64, status=SessionStatus.RUNNING))
        result.bot.strategy_version_id = v1.id
        await db.commit()
    async with session_factory() as db:
        result = await run_seed(db)
        assert result.bot.strategy_version_id == v1.id


# ADR-027 — observation bots (10-backend/03 « Bots d'observation ») -------------------------
OBS = {"paper-kraken-02": ("breakout-1h", "paper-default"), "paper-kraken-03": ("rebound", "paper-default"),
       "paper-kraken-04": ("rotation", "rotation"), "paper-kraken-05": ("pullback", "paper-default")}


async def _bots(db) -> dict:
    out = {}
    for b in (await db.scalars(select(Bot))).all():
        sv = await db.get(StrategyVersion, b.strategy_version_id)
        pv = await db.get(RiskPolicyVersion, b.risk_policy_version_id)
        out[b.name] = (b.status, sv.strategy_id, sv.status, pv.policy_id, pv.status)
    return out


async def test_observation_seed_creates_ready_bots_and_is_idempotent(session_factory):
    from app.domain.trading.enums import PolicyVersionStatus
    async with session_factory() as db:
        await run_seed(db, observation=True)
        bots, first = await _bots(db), await _counts(db)
        for name, (sid, pid) in OBS.items():
            assert bots[name] == (BotStatus.READY, sid, StrategyVersionStatus.PUBLISHED, pid,
                                  PolicyVersionStatus.PUBLISHED)
            bot = await db.scalar(select(Bot).where(Bot.name == name))
            assert (await equity_summary(db, bot.id, positions_mark_value=None)).equity == Decimal("10000")
        audits = (await db.scalars(select(AuditEvent).where(AuditEvent.reason == "ADR-027 observation paper"))).all()
        assert audits and {a.actor for a in audits} == {"system:seed"}
    async with session_factory() as db:
        await run_seed(db, observation=True)
        assert await _counts(db) == first


async def test_observation_seed_never_touches_existing_bots(session_factory):
    async with session_factory() as db:
        await run_seed(db, observation=True)
        bot = await db.scalar(select(Bot).where(Bot.name == "paper-kraken-03"))
        bot.status = BotStatus.STOPPED  # stopped by the operator after a session
        await db.commit()
        before = await _counts(db)
    async with session_factory() as db:
        await run_seed(db, observation=True)
        assert await _counts(db) == before
        bot = await db.scalar(select(Bot).where(Bot.name == "paper-kraken-03"))
        assert bot.status is BotStatus.STOPPED


async def test_observation_seed_skips_on_bot_limit_without_failing(session_factory, caplog):
    from app.domain.trading import lifecycle as lc
    async with session_factory() as db:
        await run_seed(db)
        sv = await db.scalar(select(StrategyVersion))
        pv = await db.scalar(select(RiskPolicyVersion))
        for i in range(lc.MAX_ACTIVE_BOTS - 1):
            await lc.create_paper_bot(db, name=f"other-{i}", strategy_version=sv, risk_policy_version=pv,
                                      capital=Decimal(1000), actor="user:x", reason="fill")
        await db.commit()
    async with session_factory() as db:
        with caplog.at_level("WARNING", logger="hl.seed"):
            await run_seed(db, observation=True)
        names = set(await _bots(db))
        assert not names & set(OBS)
        assert await db.scalar(select(func.count()).select_from(StrategyVersion)) == 5  # versions still created
        assert "BotLimitReached" in caplog.text

