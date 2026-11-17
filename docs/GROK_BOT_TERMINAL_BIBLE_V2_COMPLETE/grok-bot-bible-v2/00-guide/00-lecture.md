# Mode d’emploi et ordre de lecture

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Mission de ce dépôt

Ce dossier est la **source unique de vérité** du produit **0-hl** (ADR-000, ADR-001). Il spécifie une plateforme d'administration, de simulation et de supervision d'un bot de trading crypto CEX/DEX — première venue **Kraken Spot** (ADR-002) — avec le **terminal visuel de la troisième capture comme référence canonique**. Il n'est pas le code source du bot montré et n'en certifie ni la stratégie ni les performances. Il contient la V1 historique, une V2 réorganisée, quinze maquettes d'écrans, les captures observées, des contrats de démarrage et un backlog testable.

## Ordre de lecture recommandé

0. `00-guide/02-decisions.md` : décisions actées qui tranchent les `[OPEN]` et priment sur les chapitres.
1. `01-produit/` pour le périmètre, rôles et parcours ; `02-design/` pour la fidélité visuelle ; `03-ecrans/` pour les interfaces et la recette UI.
2. `04-metier/`, `05-strategies/`, `06-agents/` pour les processus, stratégies et mécanismes de décision.
3. `07-data/`, `08-risque-execution/`, `09-recherche/`, `10-backend/` pour la réalisation technique.
4. `11-securite-ops/`, `12-tests/`, `13-roadmap/` pour la mise en service et les preuves de qualité.
5. `contracts/` et `examples/` sont **des contrats de référence V2** ; ils ne remplacent pas une spécification OpenAPI exhaustive générée en build.

## Hiérarchie en cas de contradiction

1. Invariants de sécurité et interdiction de trading réel sans approbation explicite.
2. Registre des décisions (`00-guide/02-decisions.md`), pour tout point qu'il tranche.
3. Contrats d'état et de données / ledger réconcilié.
4. Critères d'acceptation testables.
5. Spécifications métier et écran.
6. Captures pour l'apparence de la vue principale.
7. Maquettes proposées pour les écrans non observés.

## Conventions

- Tous les timestamps métier sont UTC avec horodatage de réception et d'observation distincts.
- `Decimal` pour prix, quantités, soldes et PnL ; ne jamais utiliser `float` pour la comptabilité.
- Une `strategy_version` publiée est immuable ; une session référence sa version et son hash.
- Chaque ordre a une clé d'idempotence ; chaque événement a `event_id`, `correlation_id`, `sequence`.
- Écrans PNG : **illustrations de cadrage** ; la troisième capture est la seule référence canonique visuelle pour le terminal.
- La documentation précise les limites des sources et interdit toute promesse de rentabilité.

## Définition de « terminé »

Une exigence n'est terminée que si sa spécification à jour figure dans ce dossier **avant** son implémentation (ADR-000), et si elle possède un identifiant, un propriétaire de module, une implémentation, un test automatisé, un cas négatif, une trace d'audit si nécessaire, et une preuve visuelle pour l'UI. Les valeurs fictives utilisées dans les PNG ne sont pas des résultats financiers.
