# Plan de tests et critères de livraison

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Stratégie qualité

Unitaires calculs Decimal/feature/risk, properties et fuzzing, contrats Pydantic↔TypeScript, intégration DB/outbox, mocks providers, replay déterministe, tests navigateur screenshot, charge, sécurité et chaos. Chaque PR doit ajouter des tests pour invariants modifiés et runbook si incident possible.

## Cas de test prioritaires

### QA-01 — Signal sur données périmées

**Règle normative.** Un provider renvoie une valeur plus ancienne que le TTL ; la feature passe `STALE`.

**Recette.** Aucune nouvelle entrée, alerte, UI indique le retard.

### QA-02 — Token doublon

**Règle normative.** Deux pools distincts partagent le ticker.

**Recette.** Deux assets séparés par chain+contract ; aucune fusion.

### QA-03 — Stop et taille

**Règle normative.** Le stop devient plus proche et le notional demandé change.

**Recette.** Budget risque ≤ plafond, quantités correctement arrondies.

### QA-04 — Liquidité insuffisante

**Règle normative.** Sortie simulée échoue malgré prix affiché positif.

**Recette.** Trade refusé et raison de refus calculée.

### QA-05 — Ordre crash-retry

**Règle normative.** Crash après broadcast avant ACK.

**Recette.** Pas de seconde soumission avant réconciliation.

### QA-06 — Fill partiel

**Règle normative.** L’ordre n’est rempli qu’à 40%.

**Recette.** Cash réservé libéré selon politique, position de 40% et ledger équilibré.

### QA-07 — Dépôt externe

**Règle normative.** Un dépôt arrive dans le compte.

**Recette.** Equity monte, mais PnL reste inchangé.

### QA-08 — Concurrence admin

**Règle normative.** Deux utilisateurs modifient la même stratégie.

**Recette.** Le second reçoit 409 et diff, pas d’écrasement.

### QA-09 — Kill switch

**Règle normative.** Risk Officer active l’arrêt.

**Recette.** Aucune entrée nouvelle ; exits sous supervision.

### QA-10 — Reconnexion WS

**Règle normative.** Séquence 204 reçue après 200.

**Recette.** Gap détecté ; replay ou snapshot ; compteurs cohérents.

### QA-11 — Lookahead

**Règle normative.** Indicateur utilise bougie future.

**Recette.** Validateur/replay fait échouer la stratégie.

### QA-12 — Mode réduit animations

**Règle normative.** Utilisateur préfère reduced motion.

**Recette.** Aucune animation continue indispensable, statuts restent visibles.

### QA-13 — RBAC

**Règle normative.** Observer lance une commande live.

**Recette.** HTTP 403, rien exécuté, audit de la tentative.

### QA-14 — Secrets

**Règle normative.** Exécution error trace contient une clé.

**Recette.** Redaction totale validée par scanner.

### QA-15 — Drift provider

**Règle normative.** Source renvoie buy% compté et source B volume.

**Recette.** Valeurs normalisées non fusionnées sans définition explicite.

### QA-16 — Paire Kraken non tradable

**Règle normative.** Le canal `instrument` passe une paire en `cancel_only` ou `reduce_only`.

**Recette.** RUG CHECK (variante CEX) rend `FAIL pair_status` ; aucune nouvelle entrée ; positions existantes toujours supervisées.

### QA-17 — Carnet désynchronisé

**Règle normative.** Checksum `book` WS v2 invalide ou déconnexion.

**Recette.** Paire `STALE`, aucun fill paper, resouscription et snapshot ; reprise uniquement après carnet valide.

### QA-18 — Fill paper sur carnet

**Règle normative.** Achat paper dont la taille dépasse le premier niveau ask.

**Recette.** Prix moyen = moyenne pondérée des niveaux consommés, frais taker appliqués, quantités arrondies à `lot_decimals`, fill partiel si la limite de glissement est atteinte ; ledger équilibré.

### QA-19 — LIVE verrouillé

**Règle normative.** Commande `START` sur un bot `environment = LIVE` alors que l'ADR d'ouverture du LIVE n'existe pas.

**Recette.** Réponse `LIVE_NOT_APPROVED`, rien n'est soumis, audit de la tentative.

### QA-20 — Redémarrage du worker

**Règle normative.** Redéploiement (SIGTERM) au milieu d'un cycle d'agents, avec une intention d'ordre créée.

**Recette.** Après relance : aucune étape rejouée en double, au plus un ordre paper pour l'intention, curseurs cohérents, ledger équilibré.

## Critères Go/No-Go LIVE

- Réconciliation complète des ordres/fills/balances sur sessions paper représentatives.
- Test crash/restart idempotence passant avec preuve venue.
- Stress fees, liquidité et latence sur stratégies sélectionnées.
- Politique de risque et runbooks approuvés ; comptes sans droit de retrait.
- MFA et audit opérationnels ; mode LIVE inactif par défaut.
- Vérification juridique applicable et conditions des fournisseurs.
- Déploiement canary à allocation limitée, arrêt et retour arrière contrôlés.
