"""Cycle métier du worker 0-hl (app/domain/, module worker du châssis).

Le châssis (`app/modules/worker/runner.py`) fournit le conteneur, l'arrêt
propre et la cadence ; il appelle `run_cycle`.

Spécification : 06-agents/01 « Exécution sur le châssis », ADR-012 et
07-data/03 « Intégration au worker » — `run_cycle` est une **boucle longue** qui
ne rend la main qu'à l'arrêt (`stop_requested`, mis par SIGTERM à chaque
redéploiement) :
  1. amorçage idempotent des données (10-backend/03) ;
  2. flux Kraken Spot (M1.2) jusqu'à l'arrêt ;
  3. agents de décision (M1.3, 06-agents/03) en parallèle, sur l'état de marché
     en mémoire ;
  4. rapports de marché (UI-18, ADR-030), calcul lourd dans un thread.
"""

import asyncio
import logging

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import SessionLocal
from app.domain.market_reports.scheduler import MarketReportScheduler
from app.domain.trading.agents.engine import DecisionEngine
from app.domain.trading.logging_setup import configure_logging
from app.domain.trading.market.kraken_rest import KrakenRestClient
from app.domain.trading.market.kraken_ws import websockets_connector
from app.domain.trading.market.service import MarketDataService
from app.domain.trading.seed import run_seed

log = logging.getLogger("hl.worker")


async def run_cycle(db: AsyncSession, stop_requested: asyncio.Event) -> None:
    configure_logging()
    if stop_requested.is_set():
        return
    log.info("0-hl worker starting")
    seed = await run_seed(db, observation=True)
    log.info("seed ok: bot %s (%s, %s)", seed.bot.name, seed.bot.environment.value,
             seed.bot.status.value)
    async with httpx.AsyncClient(timeout=20.0) as http:
        service = MarketDataService(
            SessionLocal, KrakenRestClient(http), websockets_connector()
        )
        engine = DecisionEngine(SessionLocal, service.state)
        reports = MarketReportScheduler(SessionLocal)          # UI-18 market reports (ADR-030)
        await asyncio.gather(service.run(stop_requested), engine.run(stop_requested),
                             reports.run(stop_requested))
    log.info("0-hl worker stopped cleanly")
