"""T-M1.1-07/08/13 — strategy & policy configs: validation, immutability, hash, contract."""

import json

import pytest

from app.domain.trading.config_schemas import RiskPolicyConfig, StrategyConfig
from app.domain.trading.enums import StrategyVersionStatus
from app.domain.trading.errors import ValidationFailed, VersionImmutable
from app.domain.trading.models import StrategyVersion
from app.domain.trading.types import sha256_hex
from app.domain.trading.versioning import (
    create_policy_version,
    create_strategy_version,
    publish_policy_version,
    publish_strategy_version,
    update_policy_draft,
    update_strategy_draft,
)

from .factories import ACTOR, SPEC_ROOT, policy_config, spec_json, strategy_config

pytestmark = pytest.mark.anyio


@pytest.fixture
async def strategy_ready(db):
    """M2.3: a strategy's `risk_policy_id` must name an existing policy."""
    from app.domain.trading.models import RiskPolicy
    db.add(RiskPolicy(id=policy_config()["policy_id"], name="pd"))
    await db.flush()


async def test_example_strategy_is_valid_and_hash_excludes_status(db, strategy_ready):
    sv = await create_strategy_version(db, config=strategy_config(), name="vs", actor=ACTOR)
    assert sv.status is StrategyVersionStatus.DRAFT
    assert "status" not in sv.config
    hash_before = sv.config_sha256
    assert hash_before == sha256_hex(sv.config)
    await publish_strategy_version(db, sv, actor=ACTOR, reason="t", self_approved=True)
    assert sv.config_sha256 == hash_before


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(unknown_field=1),
        lambda c: c["entry"]["conditions"][0].update(threshold=4.0),  # float, not string
        lambda c: c["universe"].update(venues=["binance"]),
        lambda c: c["entry"].update(conditions=[]),
        lambda c: c.update(strategy_id="Bad Id"),
    ],
)
async def test_invalid_strategy_refused(db, mutation):
    cfg = strategy_config()
    mutation(cfg)
    with pytest.raises(ValidationFailed):
        await create_strategy_version(db, config=cfg, name="vs", actor=ACTOR)


async def test_published_strategy_is_immutable(db, strategy_ready):
    sv = await create_strategy_version(db, config=strategy_config(), name="vs", actor=ACTOR)
    changed = strategy_config(cooldown_seconds=60)
    await update_strategy_draft(db, sv, config=changed, actor=ACTOR)  # DRAFT: allowed
    await publish_strategy_version(db, sv, actor=ACTOR, reason="t", self_approved=True)
    with pytest.raises(VersionImmutable):
        await update_strategy_draft(db, sv, config=strategy_config(), actor=ACTOR)
    # defence in depth: direct ORM mutation is refused at flush
    sv.config = {"tampered": True}
    with pytest.raises(VersionImmutable):
        await db.flush()


async def test_draft_change_does_not_affect_published_version(db, strategy_ready):
    """INV-06: a new DRAFT never changes the hash of a published version."""
    v1 = await create_strategy_version(db, config=strategy_config(), name="vs", actor=ACTOR)
    await publish_strategy_version(db, v1, actor=ACTOR, reason="t", self_approved=True)
    published_hash = v1.config_sha256
    v2 = await create_strategy_version(
        db, config=strategy_config(version="0.9.0"), name="vs", actor=ACTOR
    )
    await update_strategy_draft(
        db, v2, config=strategy_config(version="0.9.0", cooldown_seconds=10), actor=ACTOR
    )
    await db.commit()
    db.expunge_all()
    reloaded = await db.get(StrategyVersion, v1.id)
    assert reloaded.config_sha256 == published_hash
    assert reloaded.config_sha256 != v2.config_sha256


async def test_policy_example_valid_and_immutable(db):
    pv = await create_policy_version(db, config=policy_config(), name="pd", actor=ACTOR)
    await update_policy_draft(db, pv, config=policy_config(max_spread_bps="25"), actor=ACTOR)
    await publish_policy_version(db, pv, actor=ACTOR, reason="t", self_approved=True)
    assert pv.effective_from is not None
    with pytest.raises(VersionImmutable):
        await update_policy_draft(db, pv, config=policy_config(), actor=ACTOR)
    with pytest.raises(ValidationFailed):
        await create_policy_version(
            db, config=policy_config(version="2", max_trade_risk_pct=0.5), name="pd", actor=ACTOR
        )


def _schema_props(relative: str) -> dict:
    return json.loads((SPEC_ROOT / relative).read_text(encoding="utf-8"))


def _model_fields_match_schema(model, schema: dict) -> None:
    assert set(model.model_fields) == set(schema["properties"]), model.__name__
    required = {n for n, f in model.model_fields.items() if f.is_required()}
    assert required == set(schema.get("required", [])), model.__name__


def test_contract_strategy_model_matches_json_schema():
    schema = _schema_props("contracts/strategy.schema.json")
    _model_fields_match_schema(StrategyConfig, schema)
    props = schema["properties"]
    from app.domain.trading.config_schemas import Entry, EntryCondition, Exit, Universe

    _model_fields_match_schema(Universe, props["universe"])
    _model_fields_match_schema(Entry, props["entry"])
    _model_fields_match_schema(EntryCondition, props["entry"]["properties"]["conditions"]["items"])
    _model_fields_match_schema(Exit, props["exit"])


def test_contract_policy_model_matches_json_schema():
    _model_fields_match_schema(RiskPolicyConfig, _schema_props("contracts/risk-policy.schema.json"))


def test_spec_examples_validate():
    StrategyConfig.model_validate(spec_json("examples/strategy-volume-spike.json"))
    RiskPolicyConfig.model_validate(spec_json("examples/risk-policy-paper-default.json"))
