"""Deterministic read-only tools the agents may call (spec 03-ecrans/UI-16 « Outils »).

Each tool: a JSON schema (sent to the model in `tools`), typed arguments, a JSON result. Same call ⇒
same result; no write; no personal data (actor and user fields are removed). Results over 32 KB are
truncated (`truncated: true`) and the full result's sha256 is journaled.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.agents.features import (
    BTC_IID,
    DAY_MINUTES,
    FEATURE_CATALOGUE,
    HISTORY_MINUTES,
    MINUTE,
    TREND_TOLERANCE_MINUTES,
    change_feature,
    load_minutes,
    market_view,
    scanner_features,
)
from app.domain.trading.analytics import stats_report, trade_rows
from app.domain.trading.config_schemas import validate_strategy_config
from app.domain.trading.errors import ValidationFailed
from app.domain.trading.models import (
    Bot,
    BookSnapshot,
    DomainEvent,
    FeedStatus,
    Instrument,
    RiskPolicyVersion,
    StrategyVersion,
    TradeMinute,
)
from app.domain.trading.types import sha256_hex, to_jsonable
from app.domain.trading.versioning import get_strategy_version, strategy_coherence_errors

MAX_RESULT_BYTES = 32 * 1024
_PRIVATE_KEYS = {"actor", "requested_by", "user_id", "created_by", "published_by", "granted_by",
                 "revoked_by", "acked_by", "updated_by", "email", "publish_requested_by"}


def _plain(value: Any) -> Any:
    from app.domain.ai.runner import plain
    return plain(to_jsonable_lenient(value))


def to_jsonable_lenient(value: Any) -> Any:
    try:
        return to_jsonable(value)
    except TypeError:             # floats (e.g. pydantic error context): kept as is
        return value


class ToolArgError(ValueError):
    pass


def scrub(value: Any) -> Any:
    """Remove personal fields at any depth (UI-16: no personal data in tool results)."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in _PRIVATE_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _uuid(value: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ToolArgError(f"{what} invalide : {value}") from exc


# ---------------------------------------------------------------- tools

class NoArgs(_Args):
    pass


async def list_bots(db: AsyncSession, a: NoArgs) -> Any:
    bots = (await db.scalars(select(Bot).where(Bot.deleted_at.is_(None)).order_by(Bot.name))).all()
    out = []
    for b in bots:
        sv = await db.get(StrategyVersion, b.strategy_version_id)
        pv = await db.get(RiskPolicyVersion, b.risk_policy_version_id)
        out.append({"bot_id": b.id, "name": b.name, "environment": b.environment.value,
                    "status": b.status.value,
                    "strategy": None if sv is None else f"{sv.strategy_id}@{sv.version}",
                    "strategy_version_id": b.strategy_version_id,
                    "risk_policy": None if pv is None else f"{pv.policy_id}@{pv.version}"})
    return {"bots": out}


def _trade_json(r: Any) -> dict[str, Any]:
    from app.domain.trading.api.snapshot import position_json
    return position_json(r.position) | r.extras() | {"bot_id": r.position.bot_id}


class TradeArgs(_Args):
    position_id: str


async def get_trade(db: AsyncSession, a: TradeArgs) -> Any:
    pid = _uuid(a.position_id, "position_id")
    rows = [r for r in await trade_rows(db) if r.position.id == pid]
    if not rows:
        raise ToolArgError(f"aucun trade clos {a.position_id}")
    return _trade_json(rows[0])


class TradesArgs(_Args):
    bot_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    instrument_id: str | None = None
    exit_reason: str | None = None
    limit: int = Field(20, ge=1, le=50)


async def list_trades(db: AsyncSession, a: TradesArgs) -> Any:
    rows = await trade_rows(db, bot_id=_uuid(a.bot_id, "bot_id") if a.bot_id else None, since=a.since,
                            until=a.until, instrument_id=a.instrument_id, exit_reason=a.exit_reason)
    return {"total": len(rows), "trades": [_trade_json(r) for r in rows[: a.limit]]}


class TraceArgs(_Args):
    correlation_id: str


async def get_trade_trace(db: AsyncSession, a: TraceArgs) -> Any:
    corr = _uuid(a.correlation_id, "correlation_id")
    rows = (await db.scalars(select(DomainEvent).where(DomainEvent.correlation_id == corr)
                             .order_by(DomainEvent.position))).all()
    return {"events": [{"event_type": e.event_type, "occurred_at": e.occurred_at,
                        "aggregate_type": e.aggregate_type, "payload": e.payload} for e in rows]}


class StatsArgs(_Args):
    bot_id: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    strategy_version_id: str | None = None


async def get_stats(db: AsyncSession, a: StatsArgs) -> Any:
    rows = await trade_rows(db, bot_id=_uuid(a.bot_id, "bot_id") if a.bot_id else None, since=a.since,
                            until=a.until,
                            strategy_version_id=_uuid(a.strategy_version_id, "strategy_version_id")
                            if a.strategy_version_id else None)
    return stats_report(rows)


class VersionArgs(_Args):
    strategy_id: str
    version: str


async def get_strategy_version_tool(db: AsyncSession, a: VersionArgs) -> Any:
    sv = await get_strategy_version(db, a.strategy_id, a.version)
    if sv is None:
        raise ToolArgError(f"version {a.strategy_id}@{a.version} introuvable")
    return {"version_id": sv.id, "strategy_id": sv.strategy_id, "version": sv.version,
            "status": sv.status.value, "config_sha256": sv.config_sha256, "config": sv.config}


async def list_strategy_versions(db: AsyncSession, a: NoArgs) -> Any:
    rows = (await db.scalars(select(StrategyVersion).order_by(StrategyVersion.strategy_id,
                                                              StrategyVersion.created_at))).all()
    return {"versions": [{"version_id": v.id, "strategy_id": v.strategy_id, "version": v.version,
                          "status": v.status.value, "config_sha256": v.config_sha256,
                          "entry": [f"{c['feature']} {c['op']} {c['threshold']}"
                                    for c in v.config.get("entry", {}).get("conditions", [])]}
                         for v in rows]}


async def get_feature_catalogue(db: AsyncSession, a: NoArgs) -> Any:
    return {"features": [{"name": n, "unit": u, "definition": d} for n, (u, d) in FEATURE_CATALOGUE.items()],
            "unknown_rule": "une feature non calculable vaut UNKNOWN ; une condition UNKNOWN ne passe jamais",
            "ops": ["gt", "gte", "lt", "lte"]}


class MinutesArgs(_Args):
    instrument_id: str
    until: datetime
    minutes: int = Field(60, ge=1, le=240)


async def get_market_minutes(db: AsyncSession, a: MinutesArgs) -> Any:
    rows = (await db.scalars(select(TradeMinute).where(
        TradeMinute.instrument_id == a.instrument_id, TradeMinute.minute_start < a.until,
        TradeMinute.minute_start >= a.until - MINUTE * a.minutes).order_by(TradeMinute.minute_start))).all()
    return {"instrument_id": a.instrument_id, "until": a.until,
            "minutes": [{"minute_start": r.minute_start, "open": r.open, "high": r.high, "low": r.low,
                         "close": r.close, "notional_usd": r.notional_usd, "trade_count": r.trade_count,
                         "source": r.source} for r in rows]}


class FeaturesArgs(_Args):
    instrument_id: str
    at: datetime


async def get_features(db: AsyncSession, a: FeaturesArgs) -> Any:
    """Features of the closed minute `at` by the SCANNER code (same reads as the engine)."""
    at = a.at.replace(second=0, microsecond=0)
    mins = await load_minutes(db, a.instrument_id, start=at - MINUTE * HISTORY_MINUTES, end=at)
    mins.update(await load_minutes(db, a.instrument_id, start=at - MINUTE * (DAY_MINUTES + TREND_TOLERANCE_MINUTES),
                                   end=at - MINUTE * (DAY_MINUTES - 1)))
    feats = scanner_features(mins, at)
    iids = sorted((await db.scalars(select(Instrument.id).where(Instrument.in_universe))).all())
    trends = {}
    btc_ch = change_feature("btc_change_3h", {}, at, 180)
    for iid in iids:
        m = await load_minutes(db, iid, start=at - MINUTE, end=at)
        m.update(await load_minutes(db, iid, start=at - MINUTE * (DAY_MINUTES + TREND_TOLERANCE_MINUTES),
                                    end=at - MINUTE * (DAY_MINUTES - 1)))
        if iid == BTC_IID:
            m.update(await load_minutes(db, iid, start=at - MINUTE * (180 + TREND_TOLERANCE_MINUTES),
                                        end=at - MINUTE * 179))
            btc_ch = change_feature("btc_change_3h", m, at, 180)
        trends[iid] = change_feature("trend_24h", m, at, DAY_MINUTES)
    feats.update(market_view(trends, btc_ch).for_pair(a.instrument_id))
    snap = await db.scalar(select(BookSnapshot.spread_bps).where(
        BookSnapshot.instrument_id == a.instrument_id, BookSnapshot.minute_start == at))
    out = {n: f.as_json() for n, f in sorted(feats.items())}
    out["spread_bps"] = ({"value": format(snap, "f"), "unit": "bps", "status": "OK", "reason": None,
                          "source": "instantané de carnet"} if snap is not None else
                         {"value": None, "unit": "bps", "status": "UNKNOWN", "reason": "NO_SNAPSHOT"})
    return {"instrument_id": a.instrument_id, "at": at, "features": out}


async def get_feed_status(db: AsyncSession, a: NoArgs) -> Any:
    from app.domain.trading.api.snapshot import feed_summary
    from app.domain.trading.types import utcnow
    rows = (await db.scalars(select(Instrument).where(Instrument.in_universe).order_by(Instrument.id))).all()
    feeds = {f.instrument_id: f for f in (await db.scalars(select(FeedStatus))).all()}
    return {"summary": await feed_summary(db, utcnow()),
            "pairs": [{"instrument_id": i.id, "status": i.status, "volume_24h_usd": i.volume_24h_usd,
                       "feed": None if i.id not in feeds else feeds[i.id].state} for i in rows]}


class ConfigArgs(_Args):
    config: dict[str, Any]


async def validate_strategy_config_tool(db: AsyncSession, a: ConfigArgs) -> Any:
    """The save-time validation (schema and coherence, 05-strategies/04 « Règles » 2)."""
    try:
        parsed = validate_strategy_config(a.config)
    except ValidationFailed as exc:
        return {"ok": False, "errors": [{"path": "", "message": str(exc.details or exc)[:2000]}]}
    errors = await strategy_coherence_errors(db, parsed)
    return {"ok": not errors, "errors": errors}


# ---------------------------------------------------------------- registry

@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    fn: Callable[[AsyncSession, Any], Awaitable[Any]]

    def spec(self) -> dict[str, Any]:
        schema = self.args.model_json_schema()
        schema.pop("title", None)
        schema.setdefault("properties", {})
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": schema}}


TOOLS: dict[str, Tool] = {t.name: t for t in (
    Tool("list_bots", "Liste des bots : id, nom, statut, version de stratégie et policy.", NoArgs, list_bots),
    Tool("get_trade", "Un trade clos (position) : paire, version, entrée/sortie, frais, net, R, motif, correlation_id.",
         TradeArgs, get_trade),
    Tool("list_trades", "Trades clos filtrés (bot, période ISO 8601, paire, motif), plus récents d'abord ; au plus 50.",
         TradesArgs, list_trades),
    Tool("get_trade_trace", "Événements de la trace d'un trade (features du candidat, RUG CHECK, sizing, fills, sortie).",
         TraceArgs, get_trade_trace),
    Tool("get_stats", "Statistiques M2.5 (net, brut, frais, taux de gain, profit factor, par version, paire, motif).",
         StatsArgs, get_stats),
    Tool("get_strategy_version", "Configuration complète d'une version de stratégie.", VersionArgs,
         get_strategy_version_tool),
    Tool("list_strategy_versions", "Versions de stratégie : id, statut, empreinte, conditions d'entrée.", NoArgs,
         list_strategy_versions),
    Tool("get_feature_catalogue", "Features disponibles pour les conditions : nom, unité, définition.", NoArgs,
         get_feature_catalogue),
    Tool("get_market_minutes", "Minutes closes OHLCV d'une paire avant `until` (au plus 240).", MinutesArgs,
         get_market_minutes),
    Tool("get_features", "Features calculées par le code du SCANNER à la minute close `at` pour une paire.",
         FeaturesArgs, get_features),
    Tool("get_feed_status", "État des flux de marché et des paires de l'univers.", NoArgs, get_feed_status),
    Tool("validate_strategy_config", "Valide une configuration de version de stratégie comme à l'enregistrement.",
         ConfigArgs, validate_strategy_config_tool),
)}


@dataclass
class ToolOutcome:
    result: Any
    error: str | None
    truncated: bool
    sha256: str
    size: int


async def call_tool(db: AsyncSession, name: str, raw_args: str | dict[str, Any] | None) -> ToolOutcome:
    """Validate and run one tool. Invalid arguments or a tool error are returned to the model."""
    tool = TOOLS[name]
    try:
        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        if not isinstance(args, dict):
            raise ToolArgError("les arguments doivent être un objet JSON")
        result: Any = scrub(_plain(await tool.fn(db, tool.args.model_validate(args))))
        error = None
    except (ValidationError, ToolArgError, json.JSONDecodeError) as exc:
        error = "INVALID_ARGUMENTS"
        detail = exc.errors(include_url=False) if isinstance(exc, ValidationError) else str(exc)
        result = {"error": error, "detail": _plain(detail)}
    full = json.dumps(result, ensure_ascii=False, default=str, sort_keys=True)
    size = len(full.encode("utf-8"))
    sha = sha256_hex(result)
    if size <= MAX_RESULT_BYTES:
        return ToolOutcome(result, error, False, sha, size)
    clipped = full.encode("utf-8")[:MAX_RESULT_BYTES - 200].decode("utf-8", errors="ignore")
    return ToolOutcome({"truncated": True, "partial": clipped, "full_size_bytes": size}, error, True, sha, size)
