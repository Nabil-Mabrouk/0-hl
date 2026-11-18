"""Strict validation of versioned configurations.

Mirrors `contracts/strategy.schema.json` and `contracts/risk-policy.schema.json`
(spec 10-backend/03 « Conventions techniques »). A contract test checks that the
fields of these models and the JSON schema properties stay identical.
Decimal values travel as strings (never float) and are parsed as `Decimal`.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.domain.trading.errors import ValidationFailed

_UNSIGNED_DEC = r"^[0-9]+(\.[0-9]+)?$"
_SIGNED_DEC = r"^-?[0-9]+(\.[0-9]+)?$"
_SLUG = r"^[a-z0-9_-]+$"

UnsignedDecStr = Annotated[str, StringConstraints(pattern=_UNSIGNED_DEC)]
SignedDecStr = Annotated[str, StringConstraints(pattern=_SIGNED_DEC)]
Slug = Annotated[str, StringConstraints(pattern=_SLUG)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --------------------------------------------------------------------------- #
# Strategy (contracts/strategy.schema.json)
# --------------------------------------------------------------------------- #
class Universe(_Strict):
    venues: list[Literal["kraken_spot"]] = Field(min_length=1)
    quote_currencies: list[str] | None = Field(default=None, min_length=1)
    top_n_by_volume: int | None = Field(default=None, ge=1)
    refresh_seconds: int | None = Field(default=None, ge=60)
    exclude_stablecoins: bool | None = None
    min_volume_24h_usd: UnsignedDecStr | None = None
    chains: list[str] | None = None
    token_age_min_seconds: int | None = Field(default=None, ge=0)


class EntryCondition(_Strict):
    feature: str
    op: Literal["gt", "gte", "lt", "lte"]
    threshold: SignedDecStr
    unit: str
    window_seconds: int = Field(ge=1)
    ttl_ms: int | None = Field(default=None, ge=100)


class Entry(_Strict):
    operator: Literal["AND", "OR"]
    conditions: list[EntryCondition] = Field(min_length=1)


class Exit(_Strict):
    initial_stop_pct: str
    take_profit_r: str | None = None
    max_hold_seconds: int | None = Field(default=None, ge=1)
    exit_on_status_change: bool | None = None

    @field_validator("initial_stop_pct", "take_profit_r")
    @classmethod
    def _decimal_string(cls, v: str | None) -> str | None:
        if v is not None and not re.match(_UNSIGNED_DEC, v):
            raise ValueError("must be a non-negative decimal string")
        return v


class StrategyConfig(_Strict):
    strategy_id: Slug
    version: str = Field(min_length=1)
    status: Literal["DRAFT", "IN_REVIEW", "APPROVED", "PUBLISHED", "DEPRECATED"]
    universe: Universe
    entry: Entry
    exit: Exit
    cooldown_seconds: int | None = Field(default=None, ge=0)
    risk_policy_id: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# Risk policy (contracts/risk-policy.schema.json)
# --------------------------------------------------------------------------- #
class RiskPolicyConfig(_Strict):
    policy_id: Slug
    version: str = Field(min_length=1)
    max_trade_risk_pct: UnsignedDecStr
    max_token_exposure_pct: UnsignedDecStr
    max_portfolio_exposure_pct: UnsignedDecStr
    max_daily_loss_pct: UnsignedDecStr
    max_drawdown_pct: UnsignedDecStr | None = None
    max_slippage_pct: UnsignedDecStr
    max_open_positions: int | None = Field(default=None, ge=1)
    max_spread_bps: UnsignedDecStr | None = None
    max_abnormal_move_pct: UnsignedDecStr | None = None
    min_volume_24h_usd: UnsignedDecStr | None = None
    min_liquidity_usd: UnsignedDecStr | None = None
    cooldown_seconds: int | None = Field(default=None, ge=0)
    authorization_ttl_seconds: int | None = Field(default=None, ge=1)
    kill_switch: bool | None = None

    def dec(self, field: str) -> Decimal | None:
        value = getattr(self, field)
        return None if value is None else Decimal(value)


def _validate(model: type[BaseModel], data: dict[str, Any]) -> BaseModel:
    try:
        return model.model_validate(data)
    except Exception as exc:  # pydantic.ValidationError
        raise ValidationFailed(
            f"invalid {model.__name__}", details={"errors": str(exc)}
        ) from exc


def validate_strategy_config(data: dict[str, Any]) -> StrategyConfig:
    return _validate(StrategyConfig, data)  # type: ignore[return-value]


def validate_risk_policy_config(data: dict[str, Any]) -> RiskPolicyConfig:
    return _validate(RiskPolicyConfig, data)  # type: ignore[return-value]


def stored_strategy_config(config: StrategyConfig) -> dict[str, Any]:
    """Config as stored/hashed: `status` lives in the column, not in the JSON."""
    return config.model_dump(mode="json", exclude_none=True, exclude={"status"})


def stored_policy_config(config: RiskPolicyConfig) -> dict[str, Any]:
    return config.model_dump(mode="json", exclude_none=True)
