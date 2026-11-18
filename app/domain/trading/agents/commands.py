"""Bot commands and ADR-013 auto-start (spec 06-agents/03 « Commandes »).

A command is not a success: it is processed by the worker and ends COMPLETED
or REJECTED (04-metier/02). The API (M1.4) only inserts PENDING commands.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.audit import record_audit, system_actor
from app.domain.trading.bots import transition_bot
from app.domain.trading.enums import (
    BotStatus,
    CommandStatus,
    CommandType,
    Environment,
    SessionStatus,
)
from app.domain.trading.errors import DomainError
from app.domain.trading.events import append_event
from app.domain.trading.models import Bot, BotSession, Command, RiskPolicyVersion, StrategyVersion

log = logging.getLogger("hl.agents.commands")

AUTOSTART_ACTOR = system_actor("worker")
AUTOSTART_REASON = "ADR-013 démarrage automatique PAPER"
STOP_COMMANDS = (CommandType.STOP, CommandType.EMERGENCY_STOP)


async def create_command(
    db: AsyncSession, *, bot_id: uuid.UUID, command: CommandType, actor: str,
    idempotency_key: str, reason: str | None = None, deadline_at: datetime | None = None,
) -> Command:
    existing = await db.scalar(select(Command).where(Command.idempotency_key == idempotency_key))
    if existing is not None:
        return existing
    cmd = Command(bot_id=bot_id, command=command, idempotency_key=idempotency_key, actor=actor,
                  reason=reason, deadline_at=deadline_at, status=CommandStatus.PENDING)
    db.add(cmd)
    await db.flush()
    return cmd


async def ensure_autostart(db: AsyncSession) -> list[Command]:
    """ADR-013: PAPER + READY + never had a session ⇒ one START, ever."""
    created: list[Command] = []
    bots = (await db.scalars(select(Bot).where(
        Bot.environment == Environment.PAPER, Bot.status == BotStatus.READY))).all()
    for bot in bots:
        had_session = await db.scalar(select(BotSession.id).where(BotSession.bot_id == bot.id).limit(1))
        if had_session is not None:
            continue
        cmd = await create_command(db, bot_id=bot.id, command=CommandType.START,
                                   actor=AUTOSTART_ACTOR, idempotency_key=f"autostart:{bot.id}",
                                   reason=AUTOSTART_REASON)
        if cmd.status is CommandStatus.PENDING:
            created.append(cmd)
    return created


async def _start_session(db: AsyncSession, bot: Bot, now: datetime) -> BotSession:
    sv = await db.get(StrategyVersion, bot.strategy_version_id)
    pv = await db.get(RiskPolicyVersion, bot.risk_policy_version_id)
    session = BotSession(
        bot_id=bot.id, environment=bot.environment, strategy_version_id=sv.id,
        strategy_sha256=sv.config_sha256, risk_policy_version_id=pv.id,
        risk_policy_sha256=pv.config_sha256, status=SessionStatus.RUNNING, started_at=now,
    )
    db.add(session)
    await db.flush()
    return session


async def active_session(db: AsyncSession, bot_id: uuid.UUID) -> BotSession | None:
    return await db.scalar(select(BotSession).where(
        BotSession.bot_id == bot_id, BotSession.ended_at.is_(None)
    ).order_by(BotSession.started_at.desc()).limit(1))


async def _apply(db: AsyncSession, cmd: Command, bot: Bot, now: datetime) -> dict:
    actor, reason = cmd.actor, cmd.reason or cmd.command.value
    if cmd.command is CommandType.START:
        await transition_bot(db, bot, BotStatus.RUNNING, actor=actor, reason=reason)
        session = await _start_session(db, bot, now)
        return {"session_id": str(session.id), "strategy_sha256": session.strategy_sha256}
    session = await active_session(db, bot.id)
    if cmd.command is CommandType.PAUSE_ENTRIES:
        await transition_bot(db, bot, BotStatus.PAUSED, actor=actor, reason=reason)
        if session:
            session.status = SessionStatus.PAUSED
    elif cmd.command is CommandType.RESUME_ENTRIES:
        await transition_bot(db, bot, BotStatus.RUNNING, actor=actor, reason=reason)
        if session:
            session.status = SessionStatus.RUNNING
    elif cmd.command in STOP_COMMANDS:
        await transition_bot(db, bot, BotStatus.STOPPING, actor=actor, reason=reason)
    return {"status": bot.status.value}


async def process_pending_commands(db: AsyncSession, now: datetime) -> list[Command]:
    """Process PENDING commands in request order; each outcome is audited and evented."""
    processed: list[Command] = []
    pending = (await db.scalars(select(Command).where(Command.status == CommandStatus.PENDING)
                                .order_by(Command.requested_at))).all()
    for cmd in pending:
        bot = await db.get(Bot, cmd.bot_id)
        try:
            if cmd.deadline_at is not None and now > cmd.deadline_at:
                raise _Rejection("DEADLINE_EXCEEDED", "command deadline exceeded")
            async with db.begin_nested():
                cmd.result = await _apply(db, cmd, bot, now)
            cmd.status = CommandStatus.COMPLETED
            event = "COMMAND_COMPLETED"
            log.info("command %s on %s: COMPLETED (%s)", cmd.command.value, bot.name, cmd.result)
        except (DomainError, _Rejection) as exc:
            cmd.status = CommandStatus.REJECTED
            cmd.rejection_code = exc.code
            cmd.result = {"message": str(exc)}
            event = "COMMAND_REJECTED"
            log.warning("command %s on %s: REJECTED %s", cmd.command.value, bot.name, exc.code)
            await record_audit(db, actor=cmd.actor, action="COMMAND_REJECTED", entity_type="bot",
                               entity_id=bot.id, reason=cmd.reason,
                               diff={"command": cmd.command.value, "code": exc.code})
        cmd.processed_at = now
        await append_event(db, event_type=event, aggregate_type="command", aggregate_id=cmd.id,
                           bot_id=bot.id, source="worker:commands",
                           payload={"command": cmd.command.value, "status": cmd.status.value,
                                    "rejection_code": cmd.rejection_code, "actor": cmd.actor})
        processed.append(cmd)
    await db.flush()
    return processed


async def last_stop_command(db: AsyncSession, bot_id: uuid.UUID) -> CommandType | None:
    return await db.scalar(select(Command.command).where(
        Command.bot_id == bot_id, Command.status == CommandStatus.COMPLETED,
        Command.command.in_(STOP_COMMANDS)).order_by(Command.processed_at.desc()).limit(1))


class _Rejection(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
