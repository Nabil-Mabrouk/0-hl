"""Modèles métier de 0-hl.

Point d'entrée imposé par le châssis : importer ce module enregistre toutes les
tables du domaine sur `Base.metadata` (create_all en développement, chaîne
Alembic `domain` en production — ADR-011). Le modèle physique est spécifié dans
docs/.../10-backend/03-modele-donnees-m1.md (source unique de vérité, ADR-000).
"""

from app.domain.trading.models import *  # noqa: F401,F403
from app.domain.ai.models import *  # noqa: F401,F403,E402  (UI-16 AI services)
