"""Daily budget, price table and alerts of the AI services (spec 03-ecrans/UI-16 « Budget et alertes »).

Spend of the day (UTC) = Σ over model calls of the actual cost (proxy header) or, when absent, the
cost estimated from the tokens used. Before each call the maximal cost is estimated and the call is
refused when it would cross the cap. Alerts at 80 % and 100 %, once per day and threshold.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.ai.models import AiAlert, AiSettings, AiStep
from app.domain.trading.events import append_event

log = logging.getLogger("hl.ai")
D = Decimal
ZERO = D(0)
MILLION = D(1_000_000)
DEFAULT_BUDGET = D(10)
MAX_BUDGET = D(12)                 # the proxy budget (second barrier)
DEFAULT_MODEL = "groq-gpt-oss-120b"
DEFAULT_PRICES: dict[str, dict[str, str]] = {   # USD per million tokens [OPEN] to confirm (UI-16)
    "groq-gpt-oss-120b": {"input": "0.15", "output": "0.60"},
    "groq-gpt-oss-20b": {"input": "0.075", "output": "0.30"},
}
THRESHOLDS = (80, 100)
CHARS_PER_TOKEN = 3


def default_settings() -> AiSettings:
    from app.domain.ai.services import SERVICES
    return AiSettings(id="default", daily_budget_usd=DEFAULT_BUDGET,
                      models={s: DEFAULT_MODEL for s in SERVICES}, prices=dict(DEFAULT_PRICES),
                      version=0, updated_by="system")


async def load_settings(db: AsyncSession) -> AiSettings:
    """The stored row, or the defaults (version 0, not stored) before the first change."""
    return await db.get(AiSettings, "default") or default_settings()


def model_for(settings: AiSettings, service_id: str) -> str:
    return (settings.models or {}).get(service_id) or DEFAULT_MODEL


def price(settings: AiSettings, model: str) -> tuple[Decimal, Decimal] | None:
    p = (settings.prices or {}).get(model)
    if not p:
        return None
    return D(str(p["input"])), D(str(p["output"]))


def estimate_max(prices: tuple[Decimal, Decimal], messages: list[dict[str, Any]], tools: Any,
                 max_tokens: int) -> Decimal:
    import json
    chars = len(json.dumps(messages, ensure_ascii=False, default=str)) + len(json.dumps(tools or [], default=str))
    tokens_in = chars // CHARS_PER_TOKEN + 1
    return (D(tokens_in) * prices[0] + D(max_tokens) * prices[1]) / MILLION


def estimate(prices: tuple[Decimal, Decimal] | None, tokens_in: int, tokens_out: int) -> Decimal:
    if prices is None:
        return ZERO
    return (D(tokens_in) * prices[0] + D(tokens_out) * prices[1]) / MILLION


def day_start(now: datetime) -> datetime:
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def next_reset(now: datetime) -> datetime:
    return day_start(now) + timedelta(days=1)


async def spent_today(db: AsyncSession, now: datetime) -> tuple[Decimal, Decimal, Decimal]:
    """(total, actual part, estimated part) of today's model calls."""
    rows = (await db.execute(select(AiStep.cost_actual_usd, AiStep.cost_estimated_usd).where(
        AiStep.kind == "MODEL_CALL", AiStep.at >= day_start(now)))).all()
    actual = sum((a for a, _ in rows if a is not None), ZERO)
    est = sum((e or ZERO for a, e in rows if a is None), ZERO)
    return actual + est, actual, est


@dataclass
class AlertOutcome:
    created: list[int]


Mailer = Callable[[str, str, str], None]


def _default_mailer(to: str, subject: str, body: str) -> None:
    from app.core.mailer import send_email
    send_email(to, subject, body)


async def check_alerts(db: AsyncSession, settings: AiSettings, spent: Decimal, now: datetime, *,
                       source: str = "app", force_full: bool = False,
                       mailer: Mailer | None = None) -> list[int]:
    """Create the missing alerts of the day (80 %, 100 %); e-mail each new one. Never raises on
    e-mail failure (recorded on the alert). The caller commits."""
    budget = D(settings.daily_budget_usd)
    if budget <= ZERO and not force_full:
        return []
    reached = [t for t in THRESHOLDS if force_full and t == 100 or
               (budget > ZERO and spent >= budget * D(t) / D(100))]
    day = day_start(now).date()
    existing = set((await db.scalars(select(AiAlert.threshold).where(AiAlert.day == day))).all())
    created: list[int] = []
    for t in reached:
        if t in existing:
            continue
        alert = AiAlert(day=day, threshold=t, source=source, spent_usd=spent, budget_usd=budget,
                        created_at=now)
        to = os.environ.get("ALERT_EMAIL_TO", "").strip()
        if not to:
            alert.email_error = "ALERT_EMAIL_TO absent"
        else:
            subject = f"0-hl — budget IA {'épuisé' if t == 100 else 'à 80 %'} ({spent:.4f} / {budget} USD)"
            body = (f"Dépense des services IA du {day.isoformat()} (UTC) : {spent:.4f} USD sur un plafond de "
                    f"{budget} USD (seuil {t} %, source {source}).\n"
                    + ("Plus aucun appel au modèle jusqu'à 00:00 UTC.\n" if t == 100 else "")
                    + "Réglages : vue IA, onglet Réglages (ADMIN).")
            try:
                await asyncio.to_thread(mailer or _default_mailer, to, subject, body)
                alert.email_sent = True
            except Exception as exc:  # noqa: BLE001 — never blocking (UI-16)
                alert.email_error = f"{type(exc).__name__}: {exc}"[:256]
                log.warning("AI budget alert e-mail failed: %s", alert.email_error)
        db.add(alert)
        await db.flush()
        await append_event(db, event_type="AI_BUDGET_ALERT", aggregate_type="ai_budget",
                           aggregate_id=day.isoformat(), source="api",
                           payload={"threshold": t, "spent_usd": spent, "budget_usd": budget,
                                    "source": source, "email_sent": alert.email_sent,
                                    "email_error": alert.email_error})
        created.append(t)
    return created
