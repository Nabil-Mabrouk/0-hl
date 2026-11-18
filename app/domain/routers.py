"""Routeurs métier de 0-hl.

Point d'entrée imposé par le châssis : toute variable `*_router` de ce module est
montée automatiquement par `app/core/main.py`. Spécification : 10-backend/04
(source unique de vérité, ADR-000).
"""

from app.domain.trading.api.routes import router as trading_v1_router  # noqa: F401
from app.domain.trading.api.ws import router as terminal_ws_router  # noqa: F401
