# Personas, permissions et parcours

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Matrice des rôles

| Rôle | Consultation | Édition stratégie | Validation | Opérations paper | Opérations live | Secrets |
|---|---|---|---|---|---|---|
| Observer | Oui | Non | Non | Non | Non | Non |
| Analyst | Oui | Brouillons | Non | Backtests | Non | Non |
| Operator | Oui | Non | Non | Démarrer/pauser | Selon délégation | Non |
| Risk Officer | Oui | Risk Policy | Approbation requise | Oui | Gate obligatoire | Non |
| Admin | Oui | Gestion instances | Gestion habilitations | Maintenance | Sans contournement des gates | Rotation uniquement |

## Parcours : créer et déployer une stratégie

1. Analyst crée un `DRAFT` à partir d'un template ou d'une version existante.
2. L'éditeur valide la structure, les types, les unités, les bornes et la disponibilité des données.
3. Le laboratoire lance un backtest/replay sur un dataset horodaté et figé ; génère rapport et métriques.
4. Analyst soumet `REQUEST_REVIEW` avec justification, résultats et artefacts.
5. Risk Officer vérifie les limites, accepte ou rejette ; publication crée une version immuable.
6. Operator associe la version à un bot en `PAPER`; la session enregistre hash, allocation, fournisseur et environnement.
7. Après preuve de stabilité, une procédure séparée peut envisager `LIVE` : double confirmation, MFA, absence de droits de retrait et audit.

## Parcours incident : données périmées

À dépassement d'un TTL par symbole et source, le moteur marque `STALE`, interdit les nouvelles entrées et ouvre une alerte. Les sorties déjà protégées continuent selon les mécanismes opérationnels disponibles ; le Risk Officer voit le contexte et peut demander fermeture contrôlée. Le client WebSocket reçoit `DATA_STALE` mais ne modifie jamais lui-même le risque.

## Parcours : annulation d'un ordre incertain

Avant tout retry, l'exécuteur interroge l'état distant par `client_order_id` et historise sa décision. En cas de statut non déterminé, il entre `UNKNOWN_RECONCILIATION`, bloque tout nouvel ordre pouvant dupliquer l'exposition et déclenche une intervention.

## Implémentation des rôles (ADR-006)

Les rôles ci-dessus sont des **rôles métier** stockés dans `trading_role_assignment`, distincts des rôles du châssis (`UserRole` : `user`, `admin`…). Un utilisateur peut cumuler plusieurs rôles ; chaque attribution/révocation est un `AUDIT_EVENT`. Le backend vérifie le rôle métier sur chaque route `/api/v1` et sur le WebSocket ; l'UI ne fait que masquer/désactiver.

**Mode solo (défaut).** L'opérateur unique cumule Observer, Analyst, Operator, Risk Officer et Admin. Là où le tableau exige deux validations distinctes (approbation de publication par le Risk Officer après demande de l'Analyst, passage LIVE, abaissement d'une limite), le mode solo exige : confirmation explicite récapitulative, délai de révision (défaut 10 min), MFA si disponible et audit. L'auto-approbation est autorisée **uniquement** en mode solo et est marquée `self_approved=true` dans l'audit.
