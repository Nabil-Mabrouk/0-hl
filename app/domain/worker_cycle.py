"""Cycle métier du worker 0-hl (app/domain/, module worker du châssis).

Le châssis (`app/modules/worker/runner.py`) fournit le conteneur, l'arrêt
propre et la cadence ; il appelle `run_cycle` à intervalle régulier.

Spécification : 06-agents/01 « Exécution sur le châssis » et ADR-012 — à terme
une boucle longue hébergeant le flux Kraken et les six agents (M1.2/M1.3).
Lot M1.1 : seulement l'amorçage idempotent des données (10-backend/03).

`stop_requested` est mis par SIGTERM (tout redéploiement redémarre ce
service) : chaque étape doit le vérifier pour rendre la main proprement.
"""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.trading.seed import run_seed


async def run_cycle(db: AsyncSession, stop_requested: asyncio.Event) -> None:
    if stop_requested.is_set():
        return
    await run_seed(db)
