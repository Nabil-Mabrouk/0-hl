# UI-02 — Bots & Sessions

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Fiche écran normative

**Objectif.** Gestion de bot et des sessions.

**Éléments de page.** Tableau bots, mode PAPER/LIVE, statut, capital autorisé, stratégie versionnée, uptime.

**Actions.** Créer/dupliquer/archiver ; lancer/pauser/stopper avec validation ; voir session et historique.

**Invariant critique.** Un bot LIVE ne peut être lancé sans gates approuvés..

![Vue proposée](../assets/views/UI-02.png)

## Layout précis

- Barre système persistante : identité, environnement, dernier événement serveur, statut de connexion, bouton retour terminal.
- Navigation latérale : regroupement des 15 écrans, état actif contrasté, réduction/expansion et clavier.
- Bandeau contextuel : nom de l'écran, instance courante, filtres persistés dans URL, dernière synchronisation.
- Surface métier : panneau gauche (liste / navigation), centre (données), droite (détails) selon la maquette.
- Pied : nombre de résultats / pagination, information de provenance et date des données.
- À 1440×900 : pas de scroll horizontal sur le cadre global ; une table dense peut scroller dans sa zone.

## Comportements standards

- `LOADING` : skeleton des zones de données ; aucune valeur financière artificielle.
- `EMPTY` : message métier, action autorisée pour démarrer, sans graphique trompeur.
- `STALE` : bandeau orange + âge des données et source défaillante ; calcul de trade désactivé.
- `ERROR` : code stable, correlation ID et bouton réessayer si commande idempotente.
- `FORBIDDEN` : explication de permission requise sans exposer des secrets.
- `CONFLICT` : comparer version locale/distante avant soumission.

## Contrat API et flux

Les lectures sont `GET /api/v1/...` filtrées par `bot_id`/`session_id` lorsque pertinent. Les mutations sont `POST`, `PATCH` ou commandes dédiées avec `Idempotency-Key` et `If-Match` pour configurations versionnées. Le WebSocket émet des deltas, suivis d'un snapshot de rattrapage en cas de gap de séquence. Toute mutation critique génère `AUDIT_EVENT`.

## Tests de recette

- **UI-UI-02-01** Étant donné des données nominales, lorsque l'utilisateur ouvre la vue, alors les sections et valeurs correspondent au snapshot API.
- **UI-UI-02-02** Étant donné un échec serveur, lorsqu'une action est tentée, alors aucune réussite fictive n'est affichée et le correlation ID est accessible.
- **UI-UI-02-03** Étant donné un utilisateur sans permission, lorsque l'action sensible est demandée, alors backend retourne `403` et UI affiche la restriction.
- **UI-UI-02-04** Étant donné un ordre d'événements retardé, alors l'écran conserve une vue cohérente grâce à `sequence` et snapshot.
- **UI-UI-02-05** À 1440×900 et clavier seul, toutes les actions autorisées restent accessibles ; les indicateurs de statut restent compréhensibles sans couleur.

## Machine à états de session

`CREATED → CONFIGURED → READY → STARTING → RUNNING → PAUSING → PAUSED → STOPPING → STOPPED`, plus `DEGRADED` et `FAILED`. Les transitions concurrentes rendent `409 STATE_CONFLICT`. STOPPED n’implique pas qu’aucune position n’existe : les positions sont explicitement réconciliées.
