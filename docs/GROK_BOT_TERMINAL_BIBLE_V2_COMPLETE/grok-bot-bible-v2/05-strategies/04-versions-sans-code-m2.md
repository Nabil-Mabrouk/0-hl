# Versions de stratégie et de policy sans code — conception M2.3 (ADR-021)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité), validée le 2026-11-20**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif : qu'un opérateur crée, compare, publie et retire des versions de stratégie et de risk policy, puis rattache un bot paper à une version, **sans écrire de code ni passer par l'amorçage**. Aujourd'hui (M1), une version n'existe que si elle est dans `seed_data/` et déployée ; l'API ne fait que lire.

Le domaine existe déjà (`versioning.py` : création en `DRAFT`, modification du brouillon, publication, empreinte `config_sha256`, immutabilité INV-06). M2.3 l'expose par l'API, une commande d'administration et trois écrans, et ajoute les règles ci-dessous.

## Cycle de vie

| Statut | Stratégie | Policy | Modifiable | Rattachable à un bot |
|---|---|---|---|---|
| `DRAFT` | oui | oui | oui (config entière) | non |
| `PUBLISHED` | oui | oui | **jamais** (INV-06) | oui |
| `DEPRECATED` | oui | oui | jamais | non (les bots déjà rattachés continuent jusqu'au prochain rattachement) |

`IN_REVIEW` / `APPROVED` (stratégies) restent dans le modèle pour le LIVE (ADR-009) et ne sont pas utilisés en paper. Une version publiée n'est jamais supprimée ; un brouillon peut l'être (`DELETE`, audité).

## Règles

1. **Nouvelle version** : depuis un JSON complet, ou par **duplication** d'une version existante (copie de la config en `DRAFT`, numéro de version obligatoire et unique par stratégie/policy ; l'interface propose l'incrément suivant).
2. **Validation à chaque enregistrement** : schéma (`config_schemas`) **et** cohérence :
   - chaque condition d'entrée porte sur une feature du **catalogue du SCANNER** (`volume_spike` ratio, `buy_notional_share` fraction, `price_change_5m` log_return, `spread_bps` bps, `window_notional_usd` USD, `window_trade_count` count) avec **son unité** ; une feature inconnue ou une unité différente est refusée (une condition sur une feature inexistante resterait `UNKNOWN` pour toujours) ;
   - `initial_stop_pct` > 0 et < 100 ; `take_profit_r` > 0 si présent ; `max_hold_seconds` ≥ 60 ;
   - `risk_policy_id` désigne une policy existante.
3. **Modification d'un brouillon** : concurrence optimiste par `If-Match: "<config_sha256>"` ; un brouillon modifié entre-temps ⇒ `412 PRECONDITION_FAILED`.
4. **Publication d'une stratégie** (paper) : motif obligatoire, confirmation explicite dans l'interface, audit `STRATEGY_PUBLISHED` (mode solo, ADR-006). Une référence de backtest (`backtest_ref` : empreinte de jeu de données du rapport M2.2) peut être jointe et apparaît dans l'audit ; elle n'est **pas exigée** tant que l'historique ne couvre pas 4 semaines `[OPEN]` : la rendre obligatoire ensuite.
5. **Publication d'une policy** : comparée à la version publiée la plus récente de la même policy. Si **au moins une limite est assouplie** (risque, pertes, drawdown, expositions, slippage, spread, mouvements anormaux, nombre de positions, volume ou liquidité minimum, cooldown plus court, `kill_switch` désactivé), la publication suit la règle solo d'abaissement d'une protection (ADR-006) : **demande** puis **confirmation au moins 10 minutes plus tard**, par le même utilisateur, avec motif ; avant le délai ⇒ `409 REVIEW_DELAY_PENDING`. Une policy plus stricte ou identique se publie immédiatement.
6. **Retrait** (`DEPRECATED`) : motif obligatoire ; impossible si c'est la seule version publiée d'une policy utilisée par un bot.
7. **Rattachement d'un bot** (`BIND`) à une version de stratégie et une version de policy publiées :
   - bot `PAPER` uniquement ; bot en `READY` ou `STOPPED` (aucune session ouverte, aucune position ouverte) ; sinon `409 BOT_NOT_REBINDABLE` avec l'état courant — pour changer la stratégie d'un bot qui tourne : `STOP`, attendre `STOPPED`, rattacher, `START` ;
   - la policy doit être celle désignée par `risk_policy_id` de la stratégie (autre version du même `policy_id` permise) ;
   - effet : nouvelle valeur de `strategy_version_id` / `risk_policy_version_id`, `row_version` + 1, audit `BOT_REBOUND` (avant/après), événement `BOT_REBOUND` ; un bot `STOPPED` repasse en `READY` (seule transition permise depuis `STOPPED`, faute de quoi un bot arrêté ne pourrait jamais redémarrer) ; la session suivante enregistre les nouvelles empreintes (déjà le cas au `START`) ;
   - le `START` reste une commande de l'opérateur : le démarrage automatique (ADR-013) ne concerne que le bot d'amorçage jamais démarré ;
   - rattacher aux versions actuelles est permis : c'est ainsi qu'on **réarme** un bot arrêté sans changer de stratégie.
   - L'amorçage (ADR-014) ne ré-attache ni un bot qui a déjà eu une session, ni un bot rattaché par l'opérateur (audit `BOT_REBOUND`), même jamais démarré : un rattachement par l'opérateur n'est jamais écrasé au redémarrage.
8. **Comparaison** de deux versions (même stratégie ou même policy) : différence champ par champ (`chemin`, `avant`, `après`), et pour une policy, chaque champ marqué `plus strict` / `plus souple` / `neutre`.

## Droits (01-produit/02, ADR-006)

| Action | Rôle |
|---|---|
| lire versions, comparer | Observer |
| créer, dupliquer, modifier, supprimer un brouillon de stratégie | Analyst |
| publier / retirer une stratégie | Risk Officer |
| créer, modifier, publier, retirer une policy | Risk Officer |
| rattacher un bot | Operator **et** Risk Officer (en solo : le même utilisateur cumule) |

## API (`/api/v1`, conventions de 10-backend/04)

Erreurs RFC 9457, `Idempotency-Key` sur chaque `POST` de commande, Decimals en chaînes.

| Route | Méthode | Rôle | Effet |
|---|---|---|---|
| `/strategies` · `/strategies/{id}/versions/{version}` | GET | Observer | liste ; détail (config, statut, empreinte, auteur, publication, bots rattachés) |
| `/strategies/versions` | POST | Analyst | créer un brouillon (`config`) ou dupliquer (`from_version_id`, `version`) |
| `/strategies/versions/{vid}` | PUT | Analyst | remplacer la config d'un brouillon (`If-Match`) |
| `/strategies/versions/{vid}` | DELETE | Analyst | supprimer un brouillon |
| `/strategies/versions/{vid}/validate` | POST | Analyst | validation complète sans enregistrer (erreurs par chemin) |
| `/strategies/versions/{vid}/publish` | POST | Risk Officer | publier (`reason`, `backtest_ref?`) |
| `/strategies/versions/{vid}/deprecate` | POST | Risk Officer | retirer (`reason`) |
| `/strategies/diff?a={vid}&b={vid}` | GET | Observer | différence |
| `/risk-policies…` | mêmes routes | Risk Officer | idem pour les policies ; `publish` renvoie `202` + `confirm_after` si une limite est assouplie |
| `/risk-policies/versions/{vid}/publish/confirm` | POST | Risk Officer | confirmer après le délai |
| `/features` | GET | Observer | catalogue des features du SCANNER (nom, unité, description) |
| `/bots/{bot_id}/bind` | POST | Operator + Risk Officer | rattacher (`strategy_version_id`, `risk_policy_version_id`, `reason`, en-tête `If-Match: "<row_version>"`) |

## Commande d'administration

`python -m app.domain.trading.admin …` dans le conteneur worker, pour les mêmes opérations sans interface : `strategy list|show|diff|create --file|duplicate|edit --file|publish|deprecate`, `policy …` (mêmes verbes, plus `confirm`), `bot bind`. Chaque commande exige `--as EMAIL` (utilisateur existant portant le rôle requis, vérifié comme pour l'API) et `--reason` pour publier, retirer ou rattacher. Elle appelle exactement les mêmes fonctions de domaine que l'API (mêmes validations, mêmes audits) ; `--dry-run` affiche le résultat de validation et la différence sans écrire.

## Écrans (versions M2 de UI-04, UI-05, UI-06)

Même chrome que M1 (navigation Terminal · Bots · **Stratégies** · **Risque**), mêmes états `LOADING` / `EMPTY` / `ERROR` / `FORBIDDEN`, actions masquées ou désactivées sans le rôle.

- **Stratégies (UI-04)** : une ligne par stratégie, ses versions (statut, empreinte courte, auteur, date, bots rattachés) ; actions : *Dupliquer*, *Comparer* (deux versions côte à côte, différences surlignées), *Publier* (motif, confirmation, champ facultatif « rapport de backtest »), *Retirer*.
- **Éditeur de stratégie (UI-05)** : formulaire typé généré à partir du schéma — univers ; conditions d'entrée en tableau (feature choisie dans le catalogue, unité affichée et non modifiable, opérateur, seuil, fenêtre), opérateur `AND`/`OR` ; sorties (stop %, objectif en R, durée maximale en minutes, sortie sur changement de statut) ; cooldown ; policy. Onglet *JSON* équivalent (lecture et édition). Validation à la volée par `/validate`, erreurs placées sur le champ. *Enregistrer le brouillon* ; un brouillon publié s'ouvre en lecture seule avec *Dupliquer*.
- **Risque (UI-06)** : versions de la policy ; éditeur des limites avec unité et valeur de la version publiée en regard ; chaque champ assoupli marqué en orange « plus souple » ; à la publication d'une policy assouplie, compte à rebours jusqu'à la confirmation possible.
- **Bots (UI-02, ajout)** : pour un bot `READY` ou `STOPPED`, action *Changer de stratégie* : choix d'une version de stratégie et de policy publiées, différence avec les versions actuelles, motif, confirmation. Pour un bot qui tourne, l'action est désactivée avec l'explication « arrêter le bot d'abord ».

## Hors M2.3

Approbation à deux personnes et passage `APPROVED` (multi-utilisateurs, LIVE) ; graphe visuel des conditions (UI-05 complet) ; lancement d'un backtest depuis l'écran (UI-11) ; hiérarchies de policies (UI-06 complet) ; changement de version d'un bot en cours de session.

## Tests

| ID | Vérification |
|---|---|
| T-M2.3-01 | une version publiée ne peut être ni modifiée, ni supprimée, ni republiée (`VERSION_IMMUTABLE`) ; son empreinte ne change jamais |
| T-M2.3-02 | feature hors catalogue, unité incohérente, stop ≤ 0, policy inexistante ⇒ `422` avec le chemin du champ |
| T-M2.3-03 | `If-Match` périmé sur un brouillon ⇒ `412` ; duplication ⇒ brouillon identique sauf version |
| T-M2.3-04 | policy assouplie : publication en attente, confirmation refusée avant 10 min, acceptée après ; policy plus stricte publiée immédiatement |
| T-M2.3-05 | rattachement refusé sur un bot `RUNNING`/`PAUSED`, sur une version non publiée ou retirée, sur une policy d'un autre `policy_id` ; accepté sur `STOPPED`, et la session suivante porte les nouvelles empreintes |
| T-M2.3-06 | l'amorçage au redémarrage ne défait pas un rattachement fait par l'opérateur |
| T-M2.3-07 | chaque action réservée renvoie `403` sans le rôle ; chaque mutation produit un audit avec acteur, motif et différence |
| T-M2.3-08 | la commande d'administration et l'API produisent les mêmes versions, audits et erreurs |
| T-M2.3-09 (UI) | éditeur : erreurs sur le bon champ, unité verrouillée par feature, version publiée en lecture seule ; Risque : champs assouplis signalés, compte à rebours ; Bots : action désactivée tant que le bot tourne |
