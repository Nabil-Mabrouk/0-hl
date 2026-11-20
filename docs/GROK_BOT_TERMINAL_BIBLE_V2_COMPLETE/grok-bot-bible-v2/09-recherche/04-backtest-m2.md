# Backtest M2.2 — rejeu des agents réels sur l'historique enregistré (ADR-021)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité), validée le 2026-11-20**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Réalisation M2 de [09-recherche/01](01-backtests-et-validation.md) pour Kraken Spot. Contrairement à l'étude ponctuelle de [03](03-etude-strat01.md), qui réimplémentait les sorties, le backtest fait tourner **le moteur de décision du bot** (`DecisionEngine` : SCANNER, RUG CHECK, SIZER, EXECUTOR paper, EQUITY) sur une horloge simulée. Un écart entre backtest et bot est donc un écart de données, jamais de code.

## Exécution

`python -m app.domain.trading.research.backtest --strategy FICHIER|volume-spike@0.2.0 [--policy FICHIER|paper-default@VERSION] [--from DATE] [--to DATE] [--capital 10000] [--pairs P1,P2…] [--split 0.6] [--fee-multiplier 1] [--json] [--out FICHIER]` dans le conteneur worker, **lecture seule** sur la base de production.

- `--strategy` / `--policy` : une version publiée (`id@version`) **ou** un fichier JSON brouillon validé par les mêmes schémas que la publication (`config_schemas`). Tester des paramètres ne demande donc ni code ni publication.
- Par défaut : les 30 derniers jours couverts par les instantanés de carnet, toutes les paires présentes.

## Bac à sable

- Base SQLite en mémoire, schéma `hl_*` créé depuis les modèles ; aucune écriture en production. On y crée les instruments, la version de stratégie, la version de policy et un bot `PAPER` du capital demandé (`auto_start`).
- Tout est daté en **temps simulé** : le dépôt du capital est daté d'une minute avant le début de la période (un dépôt daté à l'heure réelle serait compté comme un apport postérieur au premier instantané d'equity et simulerait une perte journalière de 100 %, correctif du 2026-11-20).
- Les commandes opérateur, l'audit et l'API n'interviennent pas.

## Horloge et données

Pas d'une minute. Pour chaque minute `m` de la période :

1. Les minutes de trades sont servies au moteur par un magasin en mémoire qui ne rend que les minutes **closes** (`minute_start < m`), avec le même contrat que la lecture du SCANNER (`load_minutes` : `live` prioritaire sur `backfill`). Le moteur reçoit ce magasin à la place de la requête en base (paramètre `minutes_loader` du `DecisionEngine`, inchangé pour le bot) : il ne peut pas lire une minute future, même par erreur (INV-03).
2. L'état de marché reçoit, pour chaque paire, le carnet de l'instantané de la minute `m` (`hl_book_snapshot`), flux `LIVE`. Sans instantané : flux `STALE` (`NO_SNAPSHOT`), donc aucune entrée et sortie différée, comme en production.
3. L'horloge est placée à `m + 30 s` (délai `window_settle_seconds` du bot) et le moteur exécute **un** tick. Les latences paper (250 ms) avancent l'horloge sans changer le carnet.

**Écarts connus avec le bot** (à afficher dans chaque rapport) : un tick par minute au lieu de toutes les 5 s, carnet capturé jusqu'à ~25 s avant la décision, aucune variation de prix entre deux instantanés (un stop ou un objectif touché puis repassé dans la minute n'est pas vu), statut `online` supposé constant, univers figé aux paires choisies.

## Coûts

Ceux du bot : frais taker 0,40 % (`PAPER_TAKER_FEE`, porté par `EngineConfig.taker_fee`, valeur inchangée pour le bot), remplissage en parcourant le carnet de l'instantané (glissement réel aux 10 niveaux enregistrés), refus si la profondeur ne suffit pas. `--fee-multiplier` multiplie les frais pour le stress (09-recherche/01 : ×2 à ×5).

## Rapport

- **En-tête de reproductibilité** : sha git du code (variable `GIT_SHA` si disponible, sinon `unknown`), empreinte SHA-256 du jeu de données (minutes et instantanés lus, dans l'ordre), empreintes des configurations de stratégie et de policy, paramètres du rejeu, période, paires.
- **Résultats**, globalement puis pour chaque partie du découpage temporel (`--split`, 60 % / 40 % par défaut) : capital initial et final, résultat net et frais, nombre de trades, taux de gain, résultat moyen par trade, profit factor, drawdown maximal, sorties par motif, entrées refusées par motif, part du résultat apportée par les trois meilleures paires.
- **Référence** : pour les mêmes sorties, la référence sans filtre d'entrée de l'étude [03](03-etude-strat01.md) sur la même période.
- **Trades** (`--json`) : chaque trade avec ses horodatages, prix, frais, motif et les features de son candidat.
- **Déterminisme** : mêmes données, mêmes configurations et mêmes paramètres ⇒ rapport identique (hors identifiants internes, non affichés).
- **Performance** : mesurée à ~85 ms par minute rejouée pour 36 paires sur un poste de développement (≈ 1 h pour 30 jours) ; `--pairs` et `--from`/`--to` réduisent le périmètre.
- **Positions ouvertes en fin de période** : non comptées dans les trades (pas de résultat réalisé), mais présentes dans le capital final au prix de liquidation.

## Hors M2.2

Écran UI-11 et registre d'expériences en base (lot ultérieur), walk-forward glissant, recherche automatique de paramètres, latence aléatoire, autres venues.

## Tests

| ID | Vérification |
|---|---|
| T-M2.2-01 | aucune donnée future : à l'instant du tick `m`, aucune minute `≥ m` n'est lisible par le moteur et seul l'instantané de `m` est appliqué |
| T-M2.2-02 | équivalence : sur un scénario enregistré (minutes + carnets), le backtest produit les mêmes candidats, entrées, sorties et le même résultat que le moteur des tests M1.3 |
| T-M2.2-03 | déterminisme : deux exécutions identiques donnent le même rapport et la même empreinte de données |
| T-M2.2-04 | instantané manquant ⇒ paire `STALE`, aucune entrée sur cette paire pendant la minute |
| T-M2.2-05 | un brouillon de stratégie invalide est refusé avec l'erreur du schéma ; un brouillon valide est rejoué sans être publié |
| T-M2.2-06 | `--fee-multiplier 2` double les frais de chaque trade ; découpage temporel : chaque trade appartient à la partie de son entrée |
| T-M2.2-07 | lecture seule : la base de production est inchangée après le rejeu |
