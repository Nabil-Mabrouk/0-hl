"""Small builders shared by domain tests."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import User, UserRole
from app.domain.trading.bots import create_bot
from app.domain.trading.enums import Environment
from app.domain.trading.models import Bot
from app.domain.trading.versioning import (
    create_policy_version,
    create_strategy_version,
    publish_policy_version,
    publish_strategy_version,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_ROOT = REPO_ROOT / "docs" / "GROK_BOT_TERMINAL_BIBLE_V2_COMPLETE" / "grok-bot-bible-v2"
ACTOR = "user:test"


def spec_json(relative: str) -> dict:
    return json.loads((SPEC_ROOT / relative).read_text(encoding="utf-8"))


def strategy_config(**overrides) -> dict:
    cfg = copy.deepcopy(spec_json("examples/strategy-volume-spike.json"))
    cfg.update(overrides)
    return cfg


def policy_config(**overrides) -> dict:
    cfg = copy.deepcopy(spec_json("examples/risk-policy-paper-default.json"))
    cfg.update(overrides)
    return cfg


async def make_user(db: AsyncSession, email: str, role: UserRole = UserRole.user) -> User:
    user = User(email=email, hashed_password="x", role=role, is_active=True)
    db.add(user)
    await db.flush()
    return user


async def make_bot(
    db: AsyncSession, *, name: str = "bot-test", environment: Environment = Environment.PAPER
) -> Bot:
    pv = await create_policy_version(db, config=policy_config(), name="pd", actor=ACTOR)
    await publish_policy_version(db, pv, actor=ACTOR, reason="test", self_approved=True)
    sv = await create_strategy_version(db, config=strategy_config(), name="vs", actor=ACTOR)
    await publish_strategy_version(db, sv, actor=ACTOR, reason="test", self_approved=True)
    return await create_bot(
        db, name=name, environment=environment, strategy_version=sv, risk_policy_version=pv,
        actor=ACTOR,
    )
