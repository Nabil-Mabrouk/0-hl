"""Cycle métier du worker 0-hl (app/domain/, module worker du châssis).

Le châssis (`app/modules/worker/runner.py`) fournit le conteneur, l'arrêt
propre et la cadence ; il appelle `run_cycle`.

Spécification : 06-agents/01 « Exécution sur le châssis », ADR-012 et
07-data/03 « Intégration au worker » — `run_cycle` est une **boucle longue** qui
ne rend la main qu'à l'arrêt (`stop_requested`, mis par SIGTERM à chaque
redéploiement) :
  1. amorçage idempotent des données (10-backend/03) ;
  2. flux Kraken Spot (M1.2) jusqu'à l'arrêt ;
  3. (M1.3) agents de décision dans la même boucle.
"""

import asyncio

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.domain.trading.market.kraken_rest import KrakenRestClient
from app.domain.trading.market.kraken_ws import websockets_connector
from app.domain.trading.market.service import MarketDataService
from app.domain.trading.seed import run_seed


async def run_cycle(db: AsyncSession, stop_requested: asyncio.Event) -> None:
    if stop_requested.is_set():
        return
    await run_seed(db)
    async with httpx.AsyncClient(timeout=20.0) as http:
        service = MarketDataService(
            SessionLocal, KrakenRestClient(http), websockets_connector()
        )
        await service.run(stop_requested)
