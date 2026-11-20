# Ledger, positions et réconciliation

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Ledger double entrée

Chaque mouvement impacte au moins deux comptes : cash, inventory cost, fees, realised PnL, gas, reserved cash. Les entrées sont append-only, avec `transaction_id` équilibré, devise, montant Decimal, time et source. Une correction est une transaction compensatrice, pas un UPDATE destructif.

## Exemple entrée paper

Achat de 100 unités à $1, frais $1 : débit inventory cost $100, débit fees $1, crédit cash $101. Le ledger financier et la table de positions projetée doivent donner une equity concordante. Un fill partiel crée son propre lot ; aucune quantité non exécutée ne figure dans inventory.

## Réconciliation

Après chaque fill, à intervalles définis et au redémarrage : récupérer ordres, fills et balances externes ; comparer aux snapshots internes ; produire `RECONCILIATION_OK` ou `RECONCILIATION_FAILED`. Écart supérieur tolérance → gel nouvelles entrées, alerte et investigation. Dépôts/retraits externes sont des cashflows, jamais du PnL.

## Pricing

Marquer les positions à une valeur liquidative conservative (simulation de sortie à profondeur actuelle ; au-delà de la profondeur visible, quantité comptée au plancher de sortie, jamais à zéro : ADR-022) et garder distinct le mark mid/spot. Réconciliation de change FX des tokens quote multiples et provenance des taux. Les statistiques historiques doivent conserver les conventions de mark et frais.

## Application paper Kraken (ADR-010)

Le capital initial (10 000 USD par défaut) est un `CashMovement` de type `DEPOSIT` : il augmente l'equity, jamais le PnL (QA-07). Base de coût : **FIFO**, figée pour tout le produit. Frais en USD (devise de cotation). Valorisation conservative : vente simulée de la position sur le carnet bid actuel ; le `mid` est affiché séparément. En paper, la « réconciliation venue » compare le ledger aux fills paper persistés et aux réservations ; elle tourne après chaque fill, au redémarrage du worker et toutes les 5 min.
