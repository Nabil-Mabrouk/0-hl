# UI-17 — Learn : rubrique publique et fiches de stratégie vivantes

> **0-hl — Bible V2.1 | 22 novembre 2026 | Statut : proposée (ADR-029), à valider par l'opérateur**
> **Traçabilité :** `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif (opérateur, 2026-11-22) : une rubrique **publique** qui explique 0-hl et chacune de ses stratégies, avec les **résultats réels** des bots paper (ADR-028, point 4), en anglais et prête pour d'autres langues. Style : 02-design/03 ; aucun nouvel écran de travail.

## Ce qui existe aujourd'hui

- La page `/learn` du **châssis** (module `tutorials`) liste des tutoriels en base, remplis par `scripts/seed_tutorials.py` lancé sur le serveur ; aucun écran d'édition ; texte seul. En production : aucun tutoriel (page vide).
- Les stratégies ne sont décrites que dans la spec (05-strategies/02, 09-recherche), en français et pour les développeurs.

## Décision proposée : une rubrique du projet, `/guide`

1. **Route du projet `/guide`** (dans `frontend/src/domain/routes.tsx`), affichée dans le shell du site ; le lien **Learn** de la Navbar (fichier du projet) y mène, pour tous les visiteurs, connectés ou non. La page `/learn` du châssis reste en place mais n'est plus liée (le module n'est pas modifié ; on pourra le réutiliser plus tard).
2. **Articles en Markdown versionnés dans le dépôt**, sous `frontend/src/domain/content/<langue>/` (`en/` seulement au départ), intégrés au build (`import.meta.glob(..., { query: "?raw" })`) : pas de base, pas de script à lancer sur le serveur, relus comme le code. Une autre langue = un dossier de plus ; à défaut d'article traduit, la version anglaise s'affiche.
3. **Brouillons relus dans la spec d'abord** : chaque article est rédigé dans `examples/learn/en/…` (source de vérité), validé par l'opérateur, puis copié dans `frontend/src/domain/content/en/…` ; un test vérifie que les deux copies sont identiques (même principe que `seed_data`).
4. **Chiffres en direct** par une API publique en lecture seule (ci-dessous) ; le texte ne contient **aucun chiffre** qui puisse vieillir sans le dire : les règles exactes, les résultats et les versions viennent de l'API ; les résultats d'étude sont datés.
5. Rendu Markdown par `react-markdown` + `remark-gfm` (déjà dans le projet), **sans HTML brut** ; liens internes par le routeur.

Alternative écartée : remplir le module `tutorials` du châssis par notre seed — écriture dans des tables du châssis, texte seul, aucun chiffre en direct.

## Contenu

### Accueil de la rubrique `/guide`
- Titre « Learn » et une phrase : « How 0-hl trades, strategy by strategy — with the real paper results. »
- **Strategies** : une carte par stratégie publiée (couleur et nom, idée en une ligne, statut du bot d'observation, résultat net en % depuis le départ, nombre de trades), triées par identifiant ; lien vers la fiche.
- **Basics** : les articles généraux (titre, résumé d'une ligne, temps de lecture).

### Fiche de stratégie `/guide/strategies/<strategy_id>`
Ordre fixe ; les blocs en direct sont insérés à l'endroit où l'article contient une ligne réservée :

| Bloc | Source | Ligne réservée |
|---|---|---|
| En-tête : nom, couleur, idée en une phrase, badges (version publiée, « Paper observation since … ») | front-matter + API | — |
| **Live result** : résultat net en %, trades, taux de gain, meilleur et pire trade en %, drawdown max en %, « as of HH:MM UTC » | API | `{{live}}` |
| **The rules** : conditions d'entrée, sortie (stop, objectif en R, sorties sur condition, durée max), délai entre deux trades, univers, taille (risque par trade de la policy) — **générées depuis la version publiée**, en phrases anglaises | API (config) | `{{rules}}` |
| **Where it buys on the radar** : petit radar statique (SVG) avec la zone visée, et lien vers le radar en direct de l'accueil | front-matter `radar_zone` | `{{radar}}` |
| **Latest trades** : 5 derniers trades clos (paire, ouverture et clôture UTC, résultat en %, motif de sortie) ; « No trade yet » sinon | API | `{{trades}}` |
| **What the study says** : date, verdict, critères passés, résumé — figés dans l'article et datés (l'étude n'est pas publique) | front-matter `study` | `{{study}}` |
| **Version history** : versions publiées (version, date, motif) | API | `{{versions}}` |
| Texte libre de l'article : l'idée, l'intuition de marché, quand elle marche, quand elle échoue, risques et limites | Markdown | — |

Une ligne réservée absente ⇒ le bloc n'est pas affiché. Une valeur inconnue ⇒ « — ». Un bot arrêté ou absent ⇒ « No paper bot running this strategy » à la place de Live result.

### Articles généraux (Basics), texte seul
1. **What 0-hl is** — paper trading, prix réels de Kraken, exécution simulée, capital fictif, aucune clé de retrait ; les étapes Scan → Check → Size → Execute → Equity.
2. **Reading the market radar** — les deux axes, BTC au centre, les fils et les étapes (renvoie à l'accueil).
3. **Fees, spread and slippage** — pourquoi ≈ 0,9 % par aller-retour tue la plupart des idées.
4. **Stops, targets and R** — le risque par trade, l'objectif en multiple du risque.
5. **How we judge a strategy** — études, période de contrôle, critères fixés à l'avance, surapprentissage, et pourquoi un bot d'observation n'est pas une validation.
6. **Glossary** — termes utilisés dans les fiches (liens depuis les fiches).

### Format d'un article

```markdown
---
id: breakout-1h            # strategy_id pour une fiche ; identifiant libre pour un article général
kind: strategy             # strategy | basics
title: Breakout 1h
strap: Buy when a pair breaks above its last-hour high on strong volume, in an uptrend.
order: 2                   # ordre d'affichage
reading_minutes: 4
radar_zone: { x: [0, 3], y: [0, 1.5] }   # pp vs BTC (1h) et log2 de la volatilité vs BTC
study: { date: 2026-11-21, verdict: "No launch", criteria: "0 of 5", summary: "…" }
---
Texte Markdown, avec les lignes réservées {{live}}, {{rules}}, {{radar}}, {{trades}}, {{study}}, {{versions}}.
```

## API publique

Sans authentification, lecture seule, cache par processus de 60 s ; **aucun montant absolu** (ni capital, ni P&L en USD, ni taille en USD), aucun identifiant interne ni donnée d'utilisateur (ADR-028).

- `GET /api/v1/public/strategies` : pour chaque stratégie ayant au moins une version publiée : `id`, `name`, `version` (dernière publiée), `published_at`, `bot` (le bot non arrêté qui l'exécute, sinon le plus récent : `name`, `status`, `since`, `net_pct`, `trades`) ou `null`.
- `GET /api/v1/public/strategies/{id}` : même tête, plus :
  - `config` : la configuration de la version exécutée par le bot (à défaut la dernière publiée) — univers, conditions d'entrée, sorties, délai — sans champ interne (`risk_policy_id` remplacé par `risk_per_trade_pct` et `max_open_positions` de la policy) ;
  - `stats` : `net_pct`, `trades`, `wins`, `losses`, `win_rate_pct`, `best_pct`, `worst_pct`, `max_drawdown_pct`, `as_of` ;
  - `trades` : 5 derniers trades clos (`symbol`, `opened_at`, `closed_at`, `result_pct` net de frais sur le montant engagé, `exit_reason`) ;
  - `versions` : versions publiées (`version`, `published_at`, `reason`).
  - Stratégie inconnue ou sans version publiée : 404.

Les résultats sont ceux du bot paper (ledger, net de frais), calculés comme dans UI-12 (Stats) ; pourcentages arrondis à 2 décimales.

## Règles en phrases (générées)

Chaque feature a un libellé anglais dans le frontend (`features.en.ts`), par exemple : `volume_spike ≥ 2.0` ⇒ « trading volume over the last 5 minutes is at least **2.0×** its usual level » ; `breakout_1h > 0` ⇒ « the price closes **above** the highest price of the previous hour » ; `trend_4h > 0` ⇒ « the price is **higher** than 4 hours ago ». Les seuils `log_return` sont affichés en % (`0.01` ⇒ « 1 % »). Feature sans libellé ⇒ forme brute `feature op seuil` (jamais d'échec d'affichage) ; un test vérifie que toutes les features du catalogue ont un libellé.

## Navigation et partage

- Navbar : **Learn** ⇒ `/guide` pour tous ; page en cours marquée.
- Chaque fiche a une URL stable ; titre du document = titre de l'article.
- Depuis la fiche : lien vers le radar en direct (accueil) et, pour un utilisateur connecté, vers le Terminal sur le bot de la stratégie (`/terminal?bot=…`).

## Tests

| ID | Vérification |
|---|---|
| T-L01 | `/public/strategies` et `/public/strategies/{id}` sans jeton ; aucun montant absolu ni identifiant interne ; 404 inconnu ; cache 60 s |
| T-L02 | statistiques identiques à celles d'UI-12 sur un jeu connu (net %, trades, taux de gain, meilleur/pire, drawdown) |
| T-L03 | règles en phrases : une phrase par condition, seuils `log_return` en %, feature inconnue en forme brute ; toutes les features du catalogue ont un libellé |
| T-L04 | une fiche insère les blocs aux lignes réservées, affiche « — » pour l'inconnu et « No paper bot running this strategy » sans bot |
| T-L05 | les articles du frontend sont identiques à leurs brouillons validés de `examples/learn/` |
| T-L06 | Markdown sans HTML brut ; langue absente ⇒ anglais |

## Livraison proposée

| Lot | Contenu |
|---|---|
| L.1 | API publique, rubrique `/guide` (accueil + modèle de fiche), lien Navbar, fiche **Breakout 1h** (article modèle ci-dessous, à valider) |
| L.2 | fiches Volume Spike, Rebound after excess, Rotation of the strongest, Pullback in an uptrend (brouillons soumis avant intégration) |
| L.3 | articles Basics 1 à 6 |

Article modèle : [`examples/learn/en/strategies/breakout-1h.md`](../examples/learn/en/strategies/breakout-1h.md).
