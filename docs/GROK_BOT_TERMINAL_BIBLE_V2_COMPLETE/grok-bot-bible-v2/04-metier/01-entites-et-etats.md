# Modèle métier, états et invariants

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Entités clés

`User`, `Role`, `Account`, `CredentialRef`, `Venue`, `Chain`, `Token`, `Pool`, `DataSource`, `Dataset`, `Strategy`, `StrategyVersion`, `RiskPolicy`, `BotInstance`, `Session`, `Candidate`, `Decision`, `RiskAssessment`, `OrderIntent`, `Order`, `Fill`, `Position`, `CashMovement`, `LedgerEntry`, `Event`, `Alert`, `BacktestRun`, `Approval`. Chaque identifiant est stable et ne se déduit pas du ticker (deux tokens peuvent partager un symbole).

## Relation canonique

Strategy 1—N StrategyVersion ; BotInstance N—1 StrategyVersion (par activation) ; Session 1—N Candidate ; Candidate 1—N Decision ; Decision 0—1 OrderIntent ; OrderIntent 1—N Order ; Order 0—N Fill ; Fills alimentent ledger et positions ; événements corrélés à tous les états et audit des changements.

## États

| Objet | États valides | Transitions interdites typiques |
|---|---|---|
| StrategyVersion | DRAFT, IN_REVIEW, APPROVED, PUBLISHED, DEPRECATED | PUBLISHED→DRAFT |
| Candidate | DISCOVERED, ENRICHING, REJECTED, APPROVED, EXPIRED | REJECTED→ORDERED sans nouvelle évaluation |
| Order | CREATED, SUBMITTED, ACKED, PARTIALLY_FILLED, FILLED, CANCELLED, REJECTED, EXPIRED, UNKNOWN | UNKNOWN→SUBMITTED nouvel ordre sans rapprochement |
| Position | PENDING, OPEN, REDUCING, CLOSED, RECONCILIATION_REQUIRED | CLOSED→OPEN via mutation silencieuse |
| Bot | CREATED, READY, RUNNING, PAUSED, STOPPING, STOPPED, DEGRADED, FAILED | STOPPED→RUNNING sans nouveau contrôle |

## Invariants numérotés

### INV-01 — Ordre jamais sans autorisation

**Règle normative.** L’exécuteur exige des décisions signées/horodatées de stratégie, risque et disponibilité des fonds.

**Recette.** Injecter un `OrderIntent` sans `risk_approval_id` doit échouer.

### INV-02 — Pas de fonds fictifs

**Règle normative.** Le ledger est le seul point d’écriture comptable ; equity est une projection.

**Recette.** Réconcilier tous les fills et cash movements avant calcul du PnL.

### INV-03 — Aucune donnée future

**Règle normative.** Un signal à t ne doit consommer que des données observables au plus tard à t.

**Recette.** Test lookahead sur historique réordonné.

### INV-04 — Reprise idempotente

**Règle normative.** Un redémarrage n’entraîne jamais deux ordres pour la même intention.

**Recette.** Simuler crash après soumission puis relance et vérifier un seul ordre distant.

### INV-05 — Rejet restrictif

**Règle normative.** Si un contrôle critique est UNKNOWN/STALE, une nouvelle entrée est refusée.

**Recette.** Injecter des données vieillies et constater `RISK_BLOCKED`.

### INV-06 — Versions persistantes

**Règle normative.** Toute session référence un hash immuable de sa configuration.

**Recette.** Changer le DRAFT n’impacte aucune session existante.

### INV-07 — Journal immuable

**Règle normative.** Chaque changement de paramètres critique est append-only avec auteur et diff.

**Recette.** Aucun `UPDATE` métier destructif sans événement compensatoire.
