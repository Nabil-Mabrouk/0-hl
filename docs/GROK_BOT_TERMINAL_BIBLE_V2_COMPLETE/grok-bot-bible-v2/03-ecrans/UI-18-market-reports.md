# UI-18 — Rapports de marché générés (Market reports)

> **0-hl — Bible V2.1 | 23 novembre 2026 | Statut : validée (ADR-030, 2026-11-23)**
> **Traçabilité :** `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif (opérateur, 2026-11-22/23) : publier automatiquement une **analyse du marché** à intervalle fixe (1 h, 4 h ou 24 h selon le réglage), rédigée par un LLM à partir de données préparées par 0-hl et des **deux rapports précédents** de la même série, illustrée de **graphiques** qui soutiennent le propos, affichée sur la page d'accueil et sur une page **Market** où l'on trie, cherche une date ou une situation. Choix de l'opérateur (2026-11-23) : séries indépendantes (4 h et 24 h actives par défaut, 1 h activable), rapports **publics**, section sur l'activité des bots incluse, Groq `gpt-oss-120b` par défaut, génération par le **worker** (accès au proxy ouvert par le châssis, commit 0ce58d3).

## Principe : le code fournit les faits, le modèle les raconte

1. Le **worker** construit, à la fin de chaque période, un **dossier de faits** déterministe (aucun LLM) et le **catalogue des graphiques** de la période.
2. Il envoie au modèle : les instructions versionnées, le dossier de faits et les deux rapports précédents de la série.
3. Le modèle rend un **JSON structuré** : titre, résumé, sections en Markdown, graphiques choisis avec leur légende, étiquettes.
4. Le code **vérifie** le rapport (chiffres, graphiques, étiquettes, ton) ; en cas d'écart, une seule nouvelle tentative avec la liste des erreurs ; sinon un **rapport « données seules »** est publié. La série ne s'interrompt jamais.
5. Le rapport est enregistré **avec ses données figées** (graphiques compris) et publié.

Le modèle ne produit jamais un chiffre ni la donnée d'un graphique : il choisit, ordonne et explique.

## Séries et calendrier

| Cadence | Fin de période (UTC) | Fenêtre | Défaut |
|---|---|---|---|
| `1h` | chaque heure pleine | 60 min | inactive |
| `4h` | 00:00, 04:00, 08:00, 12:00, 16:00, 20:00 | 240 min | **active** |
| `24h` | 00:00 | 1 440 min | **active** |

- Génération à **fin de période + 2 min** (minutes réglées, 06-agents/03), dans la boucle du worker, hors du chemin de décision des bots (tâche séparée, jamais bloquante pour le flux).
- **Une seule** génération par (cadence, fin de période) : contrainte d'unicité en base. Un worker arrêté pendant plusieurs périodes ne génère au redémarrage que la **dernière** période manquée ; les autres sont enregistrées `MISSED` (visibles dans la liste, sans contenu).
- Réglages (ADMIN, onglet *Settings* de la vue AI, UI-16) : séries actives, modèle par série (parmi les modèles tarifés). Bouton ADMIN *Generate now* pour la dernière période close d'une série (même règle d'unicité ; un rapport existant n'est jamais écrasé).

## Dossier de faits (version `facts/1`)

Calculé sur la fenêtre de la série, à partir de `hl_trade_minute` (live prioritaire), `hl_book_snapshot`, des features du SCANNER et des événements des bots. Tous les nombres sont des chaînes avec unité explicite ; chaque valeur a un identifiant stable (utilisé par la vérification).

| Bloc | Contenu |
|---|---|
| `period` | cadence, début, fin, paires suivies, paires sans données |
| `btc` / `eth` | performance (%), volatilité réalisée (% par minute, et rapport à sa médiane des 7 derniers jours de même cadence), volume (rapport à la médiane des 7 jours), plus haut / plus bas de la période |
| `breadth` | part des paires en hausse sur la période ; part des paires battant le BTC |
| `dispersion` | écart interquartile des performances (pp) ; performance médiane |
| `leaders` / `laggards` | 5 paires les plus au-dessus / au-dessous du BTC (pp, volatilité relative) |
| `events` | pics de volume (≥ 4× la normale sur 5 min), chutes rapides (≥ 4 % en 1 h), paires passées hors `online` |
| `liquidity` | écart achat/vente médian (bps) et profondeur médiane à 10 niveaux (en % de la normale) des 5 paires les plus échangées |
| `bots` | par bot actif : stratégie, signaux, refus par motif, trades ouverts / clos, résultat net de la période en % |
| `regime` | étiquettes calculées par règles (ci-dessous) |
| `changes` | écarts avec les deux rapports précédents sur les mesures clés (BTC, largeur, dispersion, volatilité) — seuls chiffres de comparaison autorisés |

**Mise en œuvre (MR.1).** Les références « 7 jours » de la volatilité du BTC et du volume sont recalculées sur les fenêtres de même durée des 7 derniers jours ; celles de la **dispersion** et de l'**écart achat/vente** viennent des rapports de la série publiés sur 7 jours (au moins 3, sinon `null`). La lecture se fait de façon asynchrone, le calcul (pur) dans un thread, pour ne jamais retenir le flux Kraken qui partage la boucle du worker ; la génération a lieu à fin de période + 2 min, après le pic de messages de Kraken en début d'heure. Une valeur incalculable vaut `null` (jamais 0) et le rapport est publié quand même.

## Étiquettes de situation (règles fixes) `[OPEN]` seuils à calibrer sur 30 jours

| Étiquette | Règle |
|---|---|
| `btc-up` / `btc-down` / `btc-flat` | performance du BTC ≥ +1 % / ≤ −1 % / entre les deux (seuils ×0,5 en 1 h, ×2 en 24 h) |
| `broad-rally` / `broad-selloff` | ≥ 70 % / ≤ 30 % des paires en hausse |
| `alt-rotation` | ≥ 60 % des paires battent le BTC |
| `btc-dominance` | ≤ 30 % des paires battent le BTC |
| `high-volatility` / `calm` | volatilité du BTC ≥ 1,5× / ≤ 0,7× sa médiane des 7 jours |
| `volume-surge` / `thin-volume` | volume total ≥ 1,5× / ≤ 0,6× sa médiane des 7 jours |
| `high-dispersion` | écart interquartile ≥ 2× sa médiane des 7 jours |
| `wide-spreads` | écart achat/vente médian des 5 premières paires ≥ 2× sa médiane des 7 jours |

Le modèle peut ajouter au plus 2 étiquettes **de la même liste**, marquées `by_model` ; la recherche les distingue.

## Catalogue de graphiques (données figées avec le rapport)

| Id | Graphique | Données |
|---|---|---|
| `radar` | instantané du Market radar en fin de période (02-design/05, échelle de la cadence) | x, y de chaque paire, positions des bots |
| `btc-breadth` | prix du BTC (ligne) et part des paires en hausse (aire, axe secondaire) | ≤ 120 points |
| `leaders` | barres horizontales : top 5 et bottom 5 face au BTC (pp) | 10 barres |
| `volatility` | volatilité relative des 10 paires les plus échangées (barres, échelle log2) | 10 barres |
| `distribution` | histogramme des performances de toutes les paires (pas 0,5 pp) | ≤ 30 classes |
| `volume` | volume par tranche (5 min en 1 h, 30 min en 4 h, 2 h en 24 h) rapporté à la normale | ≤ 24 barres |
| `liquidity` | écart achat/vente et profondeur des 5 premières paires, début vs fin | 5 × 2 |
| `bots` | signaux, refus par motif, trades par bot | 1 ligne par bot |
| `trend3` | les trois derniers rapports : BTC, largeur, dispersion | 3 × 3 points |

Le modèle cite 3 à 5 graphiques par un marqueur `{{chart:<id>}}` sur une ligne seule dans une section, avec une légende d'une phrase. `radar` et `btc-breadth` sont toujours présents dans un rapport « données seules ». Rendu SVG dans le style 02-design/03 (aucune bibliothèque nouvelle), lisible sans couleur (valeurs et libellés), alternative textuelle générée depuis les données.

## Appel au modèle

- **Agent** `market-analyst` 1.0.0 (code du projet, `app/domain/ai/agents.py`, empreinte des instructions journalisée) ; un seul appel `chat/completions` **sans outils**, `temperature` 0,3, `max_tokens` 2 500, délai 60 s ; `response_format` JSON (schéma ci-dessous) — **à valider au premier appel réel** (non testé par le châssis) ; à défaut, consigne « réponds uniquement en JSON » et extraction du premier objet JSON.
- **Instructions (résumé, en anglais)** : analyste de marché factuel pour un lecteur non spécialiste ; n'utiliser **que** les nombres du dossier et des rapports précédents, tels quels ; expliquer la **dynamique** par rapport aux deux rapports précédents ; jamais de prévision de prix, de conseil, d'objectif ni d'appel à acheter ou vendre ; décrire l'activité des bots comme des observations paper ; 350 à 700 mots ; 3 à 5 graphiques avec une légende qui dit ce qu'il faut y voir.
- **Contenu envoyé** : instructions, dossier de faits, pour chacun des deux rapports précédents de la série : période, titre, résumé, mesures clés et étiquettes (pas leur texte complet). Aucune donnée d'utilisateur ni clé.
- **Sortie attendue** :

```json
{ "title": "…", "summary": ["…", "…", "…"],
  "sections": [{ "id": "regime|leaders|volatility|changes|bots|watch", "heading": "…", "body": "Markdown, {{chart:id}} on its own line" }],
  "charts": [{ "id": "leaders", "caption": "…" }],
  "tags": ["alt-rotation"] }
```

- **Erreurs** : `budget_exceeded` ⇒ rapport « données seules », pas de relance ; `429` ⇒ une relance après 20 s ; `tool_use_failed` / délai / JSON invalide ⇒ une relance ; ensuite « données seules ».
- **Budget et journal** : mêmes règles qu'UI-16 (estimation maximale avant l'appel, coût réel ensuite, plafond journalier commun de 10 USD, alertes 80 / 100 % dans l'application et par e-mail depuis le worker) ; chaque génération est une exécution du journal IA (`hl_ai_run`, service `market-report`, `user_id` vide pour une exécution système : **migration** rendant `user_id` facultatif).

**Mise en œuvre (MR.3).** Graphiques en SVG dessinés côté client à partir des données figées du rapport (aucun appel supplémentaire), couleurs par jetons uniquement. Sur mobile, chaque graphique garde une largeur minimale de 560 px et défile horizontalement dans son cadre ; le radar reste fluide. Les valeurs négatives des barres horizontales s'écrivent du côté vide de l'axe pour ne jamais masquer le nom de la paire. La carte d'accueil utilise un lien `<a>` simple hors routeur (tests du châssis).

**Mise en œuvre (MR.2).** `response_format` = `{"type": "json_object"}` (le mode le plus largement accepté ; le schéma attendu est décrit dans les instructions et contrôlé par la vérification) — à confirmer au premier appel réel. Agent `market-analyst` 1.0.0 sans les règles communes des agents à outils (rôle autonome, en anglais). Au plus 4 appels par rapport : une relance de transport (429 après 20 s, délai, `tool_use_failed`, proxy injoignable) et une correction. Chaque appel est une étape `MODEL_CALL` du journal, chaque contrôle une étape `CHECK` avec la liste des erreurs. Une erreur inattendue du rédacteur laisse le rapport « données seules ». Réglages dans la vue AI (UI-16, ADMIN) : séries actives, modèle `market-report`, boutons *Generate 1h / 4h / 24h now* (`POST /api/v1/ai/market-reports/generate`, audit `MARKET_REPORT_REQUESTED`).

## Vérification avant publication

1. JSON conforme ; 3 points de résumé ; sections connues ; 300 à 900 mots dans les sections (les instructions demandent 350 à 700 : la vérification laisse une marge).
2. **Chiffres** : tout nombre du texte (avec ou sans %, pp, ×, bps, USD, signe ignoré) doit correspondre à une valeur du dossier ou des rapports précédents, à l'arrondi d'affichage près ; les entiers jusqu'à 31 (comptes, jours, heures) et les durées des cadences (60, 240, 1 440) sont admis ; les dates et heures doivent figurer dans le dossier. Les symboles de paires (`1INCH/USD`) sont ignorés.
3. Graphiques : 3 à 5, identifiants du catalogue, chaque marqueur cité dans le texte a sa légende et inversement.
4. Étiquettes : au plus 2, dans la liste.
5. Ton : aucune expression de la liste interdite (`buy`, `sell` à l'impératif, `price target`, `will reach`, `guaranteed`, `you should`…) ; Markdown sans HTML ni lien externe.

Écart ⇒ une seule nouvelle demande au modèle avec la liste des erreurs ; nouvel écart ⇒ « données seules ». Le rapport publié indique toujours `generated_by` : `llm` ou `data-only`.

**Rapport « données seules »** : titre et résumé produits par gabarit à partir du régime (« BTC −2.1 % · 24 % of pairs up · broad sell-off »), les graphiques `radar`, `btc-breadth`, `leaders`, `distribution`, sans texte d'analyse.

## Stockage

Table `hl_market_report` : `id`, `cadence`, `period_start`, `period_end` (unique avec `cadence`), `status` (`PUBLISHED`, `DATA_ONLY`, `MISSED`, `FAILED`), `generated_by`, `title`, `summary` (JSON), `sections` (JSON), `charts` (JSON : identifiant, légende, données figées), `facts` (JSON, dossier complet), `metrics` (colonnes : `btc_return_pct`, `breadth_pct`, `dispersion_pp`, `btc_vol_ratio`, `volume_ratio` — pour les filtres), `tags` (JSON : étiquette, source), `ai_run_id`, `model`, `instructions_sha256`, `cost_usd`, `created_at`. Conservation illimitée (quelques Ko par rapport).

## API

Publique (sans authentification, cache 60 s, aucun montant absolu — ADR-028) :
- `GET /api/v1/public/market/reports?cadence=&from=&to=&date=&tag=&q=&btc_min=&btc_max=&breadth_min=&breadth_max=&sort=period_end|btc_return|breadth&order=&limit=&offset=` — liste (sans `facts` ni données des graphiques) ;
- `GET /api/v1/public/market/reports/{id}` — rapport complet, avec les identifiants du précédent et du suivant dans la série ;
- `GET /api/v1/public/market/reports/latest?cadence=` — dernier rapport publié.

ADMIN : `POST /api/v1/ai/market-reports/generate` (`cadence`) ; réglages des séries dans `PATCH /api/v1/ai/settings` (UI-16).

## Écrans

- **Page d'accueil** (UI-00) : carte « Latest market report » sous le radar — cadence et heure, titre, les 3 points du résumé, un graphique (`leaders`), lien « Read the report ». Sélecteur 4h / 24h.
- **`/market`** (lien *Market* dans le groupe *Learn* de la Navbar) : liste des rapports (date, cadence, titre, BTC %, largeur, étiquettes) ; filtres : cadence, plage de dates ou **calendrier** (aller à une date), étiquettes de situation, curseurs BTC % et largeur ; recherche texte (titre, résumé) ; tri par date, BTC % ou largeur ; pagination.
- **`/market/<id>`** : rapport complet — titre, période, étiquettes, résumé, sections avec graphiques, pied « Generated by an AI model from 0-hl market data · facts checked automatically · not investment advice » (ou « Data-only report »), liens précédent / suivant de la série et lien vers le radar en direct.
- **Vue AI (UI-16)** : chaque génération apparaît dans le journal (service `market-report`), avec le dossier envoyé et la réponse brute.

## Tests

| ID | Vérification |
|---|---|
| T-MR-01 | dossier de faits exact sur des bougies connues (BTC, largeur, dispersion, meneurs, volatilité, liquidité, bots) ; aucune donnée postérieure à la fin de période |
| T-MR-02 | étiquettes de situation selon les règles, aux seuils |
| T-MR-03 | calendrier : une génération par période, unicité, rattrapage de la seule dernière période, `MISSED` pour les autres |
| T-MR-04 | vérification : chiffre inventé, graphique inconnu, étiquette hors liste, expression interdite ⇒ refus ; relance unique ; « données seules » ensuite |
| T-MR-05 | budget épuisé ⇒ « données seules » sans appel ; coût et journal enregistrés ; `user_id` vide accepté |
| T-MR-06 | API publique : filtres (date, étiquette, seuils, texte), tri, précédent / suivant, aucun champ sensible, cache |
| T-MR-07 | écrans : carte d'accueil, liste filtrée, calendrier, rapport avec graphiques, rapport « données seules », états vides |

## Livraison proposée

| Lot | Contenu |
|---|---|
| MR.1 — **livré (local, 2026-11-23)** | dossier de faits, étiquettes, catalogue de graphiques, table et migration (`hl_market_report`, `user_id` facultatif, `hl_ai_settings.market_series`), calendrier du worker, rapports **« données seules »**, et l'**API publique de lecture** (avancée de MR.3) ; T-MR-01..03, T-MR-06 |
| MR.2 — **livré (local, 2026-11-23)** | agent `market-analyst`, appel au modèle, vérification, relance, budget et journal, réglages des séries, *Generate now* ; premier appel réel (validation de `response_format`) ; T-MR-04, 05 |
| MR.3 — **livré (local, 2026-11-23)** | page `/market` (filtres dans l'URL, 20 par page, périodes manquées grisées), page rapport (graphiques à leur place `{{chart:id}}`, les autres en fin d'article, pied de page IA ou « données seules », précédent / suivant de la série), carte d'accueil (bascule 4h / 24h), 9 graphiques SVG avec texte alternatif ; lien *Market* public dans le groupe *More* de la navigation ; T-MR-07 |
| MR.4 | relecture des premiers rapports réels avec l'opérateur, ajustement des instructions (nouvelle version d'agent) et des seuils d'étiquettes |

Exemples : [`examples/market-report/facts-4h.json`](../examples/market-report/facts-4h.json) et [`examples/market-report/report-4h.json`](../examples/market-report/report-4h.json) (données fictives, illustratives).
