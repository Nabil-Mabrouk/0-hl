# Plusieurs bots paper — conception M2.4 (ADR-021)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité), validée le 2026-11-20**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif : faire tourner **côte à côte** plusieurs bots paper — un bot = une version de stratégie + une version de policy + un capital — pour comparer des stratégies dans les mêmes conditions de marché, sans code. M2.3 a donné les versions et le rattachement, M2.5 les statistiques par bot et par version ; il manque la création des bots et l'isolation complète de leur périmètre.

## Ce qui existe déjà `[OBS]` (code M1–M2.3)

- Le moteur de décision traite tous les bots à chaque tick : sorties de toutes les positions, entrées des bots `RUNNING`, EQUITY par bot ; état d'exécution séparé par bot.
- Cash, ledger, positions, perte du jour, drawdown, cooldown et réconciliation sont **par bot** ; chaque bot lit sa propre policy.
- Le flux de marché s'abonne à l'**union** des univers des bots actifs (`READY`, `RUNNING`, `PAUSED`, `DEGRADED`) et des paires portant une position surveillée.
- Terminal (`?bot=`), Bots, Trades et Stats savent afficher un bot donné.

## Ce que M2.4 ajoute

1. **Univers propre à chaque bot.** Aujourd'hui, chaque bot évalue toutes les paires de l'univers commun. Désormais, un bot n'évalue que les paires de **son** univers : les paires de l'univers commun qui satisfont sa stratégie (`top_n_by_volume` sur le volume 24 h, `min_volume_24h_usd`, `quote_currencies`, exclusion des stablecoins). `SCAN_CYCLE_COMPLETED` et le compteur « checked » ne portent que sur ces paires. Les sorties ne dépendent jamais de l'univers (une position reste surveillée même si sa paire en sort).
2. **Création d'un bot** par l'API, la commande d'administration et l'écran Bots :
   - nom unique (3 à 64 caractères, minuscules, chiffres, `-`), environnement **`PAPER` uniquement** (ADR-009) ;
   - une version de stratégie et une version de policy **publiées**, la policy étant celle désignée par la stratégie (même règle que le rattachement M2.3) ;
   - capital paper initial entre **100** et **1 000 000 USD** (10 000 par défaut), déposé à la création (mouvement de capital audité, jamais un gain) ;
   - le bot naît `READY` ; il ne démarre **que sur commande `START`** de l'opérateur (le démarrage automatique ADR-013 reste réservé au bot d'amorçage) — correctif du 2026-11-20 : le démarrage automatique s'appliquait à tout bot `PAPER` `READY` jamais démarré, il est désormais limité au bot d'amorçage `paper-kraken-01` ;
   - audits `BOT_CREATED` (versions) et `BOT_CREATED_BY_OPERATOR` (acteur, motif, versions, capital).
3. **Plafond de bots actifs** : au plus **5** bots non `STOPPED` à la fois, pour borner la charge du worker (chaque bot évalue son univers à chaque minute) ; au-delà ⇒ `409 BOT_LIMIT_REACHED`. Un bot `STOPPED` ne compte pas.
4. **Terminal** : sélecteur de bot dans l'en-tête (liste des bots, statut) ; le choix est conservé dans l'URL (`?bot=`).
5. **Recette** : `python -m app.domain.trading.recette --bot NOM` existe déjà ; sans `--bot`, le rapport porte sur le bot d'amorçage, puis une ligne de résumé par autre bot actif (statut, trades, résultat net).

## Règles d'isolation (rappel, désormais testées)

- Deux bots peuvent tenir une position sur la **même paire** ; elles sont indépendantes (cash, stop, objectif, sortie).
- La perte du jour, le drawdown, le `kill_switch` et le nombre maximal de positions d'un bot n'ont **aucun** effet sur un autre bot.
- Un `EMERGENCY_STOP` ne liquide que les positions du bot visé.
- **Limite connue de la simulation paper** : les remplissages de plusieurs bots sur la même paire au même instant sont calculés chacun sur le carnet complet ; un bot ne consomme pas la liquidité vue par l'autre. Acceptable tant que les tailles restent petites devant la profondeur ; à revoir avant tout LIVE.

## Droits

Créer un bot : **Operator et Risk Officer** (en solo : le même utilisateur cumule), comme le rattachement. Démarrer, mettre en pause, arrêter : inchangé (Operator ; `EMERGENCY_STOP` aussi Risk Officer).

## API (`/api/v1`)

| Route | Méthode | Rôle | Effet |
|---|---|---|---|
| `/bots` | POST | Operator + Risk Officer | créer (`name`, `strategy_version_id`, `risk_policy_version_id`, `capital_usd`, `reason`) ⇒ `201` + bot `READY` |
| `/bots` | GET | Observer | inchangé, plus `strategy` / `risk_policy` (identifiant et version) et `capital_usd` initial pour chaque bot |

Erreurs : `422 VALIDATION_FAILED` (nom, capital, policy incohérente), `409 STATE_CONFLICT` (nom déjà pris), `409 STRATEGY_NOT_PUBLISHED`, `409 BOT_LIMIT_REACHED`.

## Commande d'administration

`… admin --as EMAIL bot create NOM --strategy ID@VER --policy ID@VER --capital 10000 --reason "…"` ; mêmes règles et audits que l'API.

## Écrans

- **Bots (UI-02)** : bouton *New bot* (Operator + Risk Officer) ouvrant un formulaire : nom, version de stratégie publiée, version de policy (filtrée sur celle qu'exige la stratégie), capital, motif ; erreurs sur le champ ; à la création, le bot apparaît `READY` avec son bouton `START`. Le tableau affiche le capital initial, la stratégie et la policy de chaque bot.
- **Terminal (UI-01)** : sélecteur de bot dans l'en-tête.

## Hors M2.4

Archivage ou suppression d'un bot, apports et retraits après la création, partage de liquidité entre bots, allocation automatique de capital, bots `LIVE`.

## Tests

| ID | Vérification |
|---|---|
| T-M2.4-01 | création : bot `READY`, capital déposé (ledger équilibré, apport et non gain), audit ; refus sur nom pris, nom invalide, capital hors bornes, version non publiée, policy d'un autre `policy_id`, environnement autre que `PAPER` |
| T-M2.4-02 | plafond : le 6ᵉ bot non arrêté est refusé (`BOT_LIMIT_REACHED`) ; un bot `STOPPED` ne compte pas |
| T-M2.4-03 | univers par bot : un bot `top_n = 1` n'évalue que la première paire par volume ; un autre bot, toutes ; `SCAN_CYCLE_COMPLETED` porte sur l'univers du bot |
| T-M2.4-04 | isolation : deux bots en position sur la même paire ; la perte du jour, le `kill_switch` ou `max_open_positions` de l'un ne bloque pas l'autre ; `EMERGENCY_STOP` d'un bot ne touche pas l'autre ; cash et réconciliation séparés |
| T-M2.4-05 | un nouveau bot ne démarre pas tout seul ; `START` crée sa session avec ses empreintes |
| T-M2.4-06 | commande d'administration et API : mêmes règles et audits |
| T-M2.4-07 (UI) | formulaire *New bot* (policy filtrée, erreurs sur le champ, création ⇒ ligne `READY`) ; bouton absent sans les deux rôles ; sélecteur de bot du Terminal conservé dans l'URL |
