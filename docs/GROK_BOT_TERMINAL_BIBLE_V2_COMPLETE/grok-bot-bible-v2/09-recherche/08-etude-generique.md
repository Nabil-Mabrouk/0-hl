# Étude générique d'une version de stratégie (ADR-025)

> **0-hl — Bible V2.1 | 21 novembre 2026 | Statut : spécification de référence (source unique de vérité), validée le 2026-11-21**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Les études 09-recherche/03, /05, /06 et /07 sont écrites une par stratégie. Or une version de stratégie est une **donnée** (conditions d'entrée et de sortie, stop, objectif, durée, cooldown, policy) évaluée par le code du SCANNER. Un seul outil peut donc étudier **n'importe quelle version**, publiée ou brouillon, et une grille de variations de ses seuils, avec la méthode et les critères déjà fixés. Une nouvelle idée devient testable sans code : on la décrit dans l'éditeur (UI-05), on l'étudie, on la juge.

## Exécution

```
python -m app.domain.trading.research.study (--strategy ID@VER | --file F.json)
       [--grid G.json] [--days N] [--min-days D] [--min-trades T] [--order-by FEATURE] [--json]
```

Dans le conteneur worker, **lecture seule** (aucune écriture en base, jamais importé par le worker ni par l'API). `--strategy` lit une version en base (tout statut) ; `--file` lit un JSON de version (validé comme à l'enregistrement : schéma **et** cohérence, 05-strategies/04 « Règles » 2 ; une version incohérente est refusée avec les chemins des champs).

## Ce qui est rejoué

- **Univers** : les paires que la stratégie sélectionnerait aujourd'hui (même règle que le moteur, `bot_universe` : cotation, stablecoins, volume minimum, top N).
- **Features** : à chaque minute close `m` et pour chaque paire, **toutes les features utilisées** par la version (entrée et sortie), calculées par le code du SCANNER : features de la paire, lecture étroite à 24 h et **vue de marché** (06-agents/03) quand la version en utilise. Aucune donnée postérieure à `m` (INV-03).
- **Spread** : `spread_bps` est lu dans l'**instantané de carnet** de la minute (`hl_book_snapshot`, M2.1). Sans instantané, une condition sur le spread est **supposée vraie** et la minute est comptée dans le rapport (« spread supposé »).
- **Entrée** : verdict `evaluate_conditions` du SCANNER (`PASS` seulement ; `UNKNOWN` n'entre jamais). Une position par paire ; `cooldown_seconds` de la version après chaque sortie ; au plus `max_open_positions` de la policy rattachée positions simultanées. Quand plusieurs paires sont candidates à la même minute pour les places restantes : ordre alphabétique de l'identifiant, ou croissant d'une feature avec `--order-by` (par exemple `rs_rank_24h`).
- **Prix et coûts** : ceux des études précédentes (entrée au `close` de la minute `m − 1` + 0,10 %, frais taker 0,40 % par côté, stop avec 0,10 % de glissement supplémentaire).
- **Sortie**, dans cet ordre à chaque minute de détention : stop (`initial_stop_pct`) ; objectif (`take_profit_r`, si présent) ; **conditions de sortie** (`exit.conditions`, évaluées sur les minutes antérieures, sortie au `close` de la minute précédente ; `UNKNOWN` ne sort jamais) ; durée maximale (`max_hold_seconds`). Sans durée maximale, une position encore ouverte à la fin des données est exclue (« ouverte à la fin »). Minute manquante pendant la détention ⇒ trade exclu.
- **Référence** : une entrée par paire et par heure pleine, sans condition d'entrée, avec les sorties et le cooldown de la version, sans limite de positions simultanées (dérive du marché moins les frais).
- **Non rejoué** : `exit_on_status_change` (l'historique des statuts de paire n'est pas conservé). Le spread de l'instantané est celui de la minute de décision `m` (capturé au début de la minute).

## Grille

Fichier JSON facultatif : chemin d'un champ de la version → liste de valeurs ; le produit cartésien donne les variantes.

```json
{ "entry.conditions[3].threshold": ["-0.02", "-0.03", "-0.05"],
  "exit.initial_stop_pct": ["2", "3", "4"],
  "exit.max_hold_seconds": [21600, 43200] }
```

- Chaque variante est validée comme une version (schéma et cohérence) ; une variante invalide arrête l'étude avec son chemin.
- **Au plus 500 variantes** ; au-delà, refus (risque de faux positif et durée).
- Les features sont calculées **une seule fois** par paire et par minute, puis partagées par toutes les variantes : une variante ne change que des seuils et des sorties. Elles sont conservées en flottants double précision (mémoire), comparées à un seuil converti de la même façon : même verdict que le SCANNER (T-R41).

## Jugement

- **Sans grille** : la version elle-même, jugée sur les deux parties (60 % / 40 %), sans classement.
- **Avec grille** : classement des variantes sur la partie 1 (au moins 10 trades), jugement de la meilleure sur la partie 2 ; la version de départ et la référence sont toujours rapportées.
- **Critères de lancement** : ceux de 09-recherche/05 (≥ 20 trades sur la partie 2, net moyen > 0, profit factor ≥ 1,2, brut ≥ référence + 0,3 point, net de la partie 1 > 0) ; `--min-trades` et `--min-days` (verdict « NON JUGEABLE » sous ce nombre de jours) permettent les adaptations déjà décidées pour une stratégie (STRAT-11 : 15 trades, 28 jours). Le choix de ces options est consigné dans la spec de la stratégie **avant** l'exécution.

## Rapport

Celui des études précédentes (période, coupure, paires, variantes testées, hypothèses ; version de départ, référence, meilleures variantes, verdict), complété de deux diagnostics :

- **taux de passage de chaque condition d'entrée** (part des minutes évaluables où elle est `PASS`, `FAIL`, `UNKNOWN`) : montre quelle condition bloque, et si la stratégie a simplement eu l'occasion de trader (par exemple un filtre de régime qui n'est jamais vrai en marché baissier) ;
- part des minutes où le **spread** a été supposé faute d'instantané.

En-tête de reproductibilité : empreinte de la configuration (`config_sha256`), de la grille, période et nombre de minutes lues.

## Relation avec les études existantes

Les études STRAT-01/02/06/11 restent disponibles. L'étude générique devient l'outil par défaut pour toute nouvelle stratégie. Un test vérifie qu'elle reproduit, sur des données synthétiques, le résultat de l'étude STRAT-02 pour la même version (même signal, mêmes sorties, mêmes coûts).

## Hors périmètre

Lancement depuis l'écran (UI-11) et registre des expériences ; exécution maker (ordres limites, non remplis) ; sizing en USD (les résultats restent en % par trade) ; ventes à découvert.

## Tests

| ID | Vérification |
|---|---|
| T-R41 | même décision d'entrée que le moteur pour une minute et une paire données (mêmes features, même verdict) |
| T-R42 | sorties : ordre stop → objectif → condition → durée ; `UNKNOWN` ne sort jamais ; sans durée, position ouverte à la fin exclue |
| T-R43 | grille : produit cartésien, variante invalide refusée avec son chemin, refus au-delà de 500 variantes ; features calculées une fois |
| T-R44 | `max_open_positions` respecté ; ordre des candidates alphabétique ou par `--order-by` |
| T-R45 | spread lu dans l'instantané ; sans instantané, supposé vrai et compté |
| T-R46 | taux de passage par condition exacts sur un jeu connu ; `--min-days` ⇒ « NON JUGEABLE » |
| T-R47 | reproduit l'étude STRAT-02 pour la même version sur des données synthétiques |
