# Market radar — figure centrale de l'accueil et du Terminal

> **0-hl — Bible V2.1 | 22 novembre 2026 | Statut : validée (ADR-028, 2026-11-22)**
> **Traçabilité :** `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif : montrer en un coup d'œil quelles paires surperforment le BTC, lesquelles sont plus volatiles, et ce que chaque bot vient d'en conclure. Principe proposé par l'opérateur le 2026-11-22 ; maquette de référence « Market radar » (02-design/03).

## Ce que montre la figure

| Élément | Signification |
|---|---|
| **Centre** | BTC/USD, la référence. Une paire proche du centre a une performance et une volatilité proches de celles du BTC sur la fenêtre. |
| **Axe horizontal** | performance de la paire sur la fenêtre glissante **moins** celle du BTC, en points de pourcentage (pp). À droite : elle bat le BTC ; à gauche : elle fait moins bien. |
| **Axe vertical** | volatilité réalisée de la paire **rapportée** à celle du BTC sur la même fenêtre, en échelle logarithmique de base 2 : ×2 et ×0,5 sont à la même distance du centre. Au-dessus : plus volatile ; en dessous : moins. |
| **Point** | une paire de l'univers commun (≈ 40 paires USD). Grossi et nommé : BTC, la paire sélectionnée, les paires actives du cycle. |
| **Anneau** autour d'un point | un bot détient une position ouverte sur cette paire (couleur de sa stratégie ; plusieurs anneaux si plusieurs bots). |
| **Fil** d'un point vers une carte de bot | ce bot vient d'évaluer cette paire. Couleur de la stratégie. Signal ⇒ l'impulsion continue dans les étapes de la carte ; pas de signal ⇒ le fil s'éteint à l'entrée de la carte. |
| **Cartes de bots** (à droite, `BotCard`) | un bot par carte : nom, stratégie, statut, ligne d'état (« 40 scanned · 1 signal », « SOL/USD · Spread too wide »…), étapes Scan → Check → Size → Execute → Equity. |
| **Barre d'outils** | « Market radar » · `Live · N pairs · cycle HH:MM UTC` ; Pause / Replay ; dans le Terminal, choix de fenêtre 1h / 4h / 24h. |
| **Pied** | sélecteur « Inspect pair » (toutes les paires, clavier) et lecture exacte : `SOL/USD · +1.40 pp vs BTC · 2.00× BTC volatility · 58 min traded` ; rappel « <fenêtre> rolling window · BTC reference ». |

Exemple : BTC +1,0 % sur l'heure, SOL +2,4 % ⇒ SOL à +1,4 pp ; volatilité de SOL deux fois celle du BTC ⇒ SOL dans le quadrant supérieur droit, à `log2(2) = +1`.

Lecture par stratégie (reprise dans les articles d'Apprendre) : Rotation (STRAT-11) achète à droite ; Rebond (STRAT-06) à gauche, souvent en haut ; Breakout (STRAT-02) à droite et en haut ; Pullback (STRAT-12) près du centre, légèrement à gauche, dans un marché haussier.

## Calcul (serveur)

Source : `hl_trade_minute` (bougies d'une minute, `source = live` en priorité, `backfill` à défaut pour une minute absente en live). Fenêtre `W` ∈ {60, 240, 1440} minutes, terminée à la **dernière minute réglée** (même règle que le moteur, 06-agents/03).

1. **Grille** : les `W` minutes de la fenêtre ; clôture de chaque minute = `close` de la bougie, **reportée** de la minute précédente si la minute n'a aucun échange. Clôture de départ = dernière clôture connue **au plus tard** au début de la fenêtre (recherche limitée à 24 h avant).
2. **Performance** `r = close_fin / close_départ − 1` ; `excess_pp = (r_paire − r_BTC) × 100`.
3. **Volatilité** : écart type des rendements logarithmiques minute par minute sur la grille (W rendements) ; `vol_ratio = vol_paire / vol_BTC` ; `y = log2(vol_ratio)`.
4. **Jugeable** : au moins `W/3` minutes avec échanges (20 sur 60). Sinon la paire est affichée **en creux et grisée sur l'axe horizontal** (`y = 0`), avec la mention « thin trading » dans la lecture ; sa performance reste affichée. Pas de clôture de départ ou de fin ⇒ paire omise et comptée dans `pairs_without_data`. BTC non jugeable ⇒ figure en état `stale` (« BTC reference unavailable »).
5. **Échelles fixes** (pas d'ajustement automatique) : x ∈ ±5 pp (1h), ±10 pp (4h), ±20 pp (24h) ; y ∈ [−2, +2] (×0,25 à ×4). Valeur hors échelle ⇒ point plaqué au bord avec un chevron vers l'extérieur ; la lecture donne la valeur exacte.
6. Calcul en `Decimal` pour les rendements, flottant permis pour l'écart type et le `log2` (affichage) ; arrondis : `excess_pp` 2 décimales, `vol_ratio` 2 décimales.

## Ce que chaque bot a conclu (worker)

`SCAN_CYCLE_COMPLETED` (10-backend/04) gagne le champ **`outcomes`** : pour chaque paire évaluée par le bot dans la fenêtre, son résultat :

| Valeur | Sens | Étapes allumées sur la carte |
|---|---|---|
| `NO_SIGNAL` | conditions d'entrée non remplies | Scan (le fil s'éteint à l'entrée) |
| `UNKNOWN` | feature indisponible (`UNKNOWN`) | Scan, mention « data unknown » |
| `COOLDOWN` | délai depuis la dernière sortie non écoulé (seul critère refusé par RUG CHECK) | Scan |
| `HELD` | conditions remplies mais position déjà ouverte sur la paire | Scan |
| `SIGNAL` | signal détecté, contrôle en cours (état transitoire, remplacé par le résultat final du cycle) | Scan, Check |
| `FILTERED` | signal refusé par RUG CHECK (spread, profondeur…), avec le motif | Scan, Check en `stop` |
| `REFUSED` | signal refusé par SIZER (capital, positions max…), avec le motif | Scan, Check, Size en `stop` |
| `NOT_FILLED` | taille approuvée mais ordre non exécuté (données périmées, autorisation expirée…), avec le motif | Scan, Check, Size, Execute en `stop` |
| `ENTERED` | ordre paper exécuté | Scan, Check, Size, Execute |

Forme : `"outcomes": {"SOL/USD": {"r": "ENTERED"}, "ETH/USD": {"r": "FILTERED", "why": "spread_bps"}, …}` ; les paires sans entrée dans `outcomes` n'ont pas été évaluées. Volume : un événement par bot et par minute, ≈ 40 entrées courtes. L'étape **Equity** s'allume à la clôture d'une position (`POSITION_CLOSED`), indépendamment du cycle.

## API

Même calcul pour les deux routes, résultat mis en cache par processus et par fenêtre (15 s pour 1h et 4h, 60 s pour 24h).

- `GET /api/v1/public/radar` — **sans authentification**, fenêtre 1h seulement ; page d'accueil.
- `GET /api/v1/market/radar?window=1h|4h|24h` — rôles de lecture (Observer et plus) ; Terminal.

```json
{
  "generated_at": "…", "window_minutes": 60, "window_end": "2026-11-22T14:32:00Z", "stale": false,
  "reference": {"symbol": "BTC/USD", "return_pct": "1.00", "volatility_pct": "0.08"},
  "pairs": [{"symbol": "SOL/USD", "return_pct": "2.40", "excess_pp": "1.40", "vol_ratio": "2.00",
             "y": "1.0000", "minutes_traded": 58, "judgeable": true}],
  "pairs_without_data": 1,
  "bots": [{"name": "paper-kraken-02", "status": "RUNNING",
            "strategy": {"id": "breakout-1h", "version": "0.1.0", "name": "Breakout 1h"},
            "net_pct": "-0.42",
            "last_cycle": {"window_end": "…", "evaluated": 40, "signals": 1,
                           "outcomes": {"SOL/USD": {"r": "ENTERED"}}},
            "open_positions": ["SOL/USD"]}]
}
```

`y` est une chaîne (le JSON du domaine refuse les flottants), `"0"` pour une paire non jugeable ; `stale` = BTC non jugeable. `window_end` est la fin exclusive de la fenêtre (début de la minute qui suit la dernière minute réglée, 30 s après sa clôture). `bots` : bots `RUNNING` ou `PAUSED` (ordre : nom) ; `net_pct` = résultat net depuis le premier dépôt, en % du capital déposé (ADR-028 : résultats publics) ; `last_cycle` = dernier `SCAN_CYCLE_COMPLETED` du bot depuis moins de 5 min, sinon `null`. Aucun montant absolu, identifiant interne ni donnée d'utilisateur. Paires triées par symbole ; BTC exclu de `pairs` (il est `reference`).

## Animation (02-design/04)

Cycle rejoué pour chaque **nouvelle** `window_end` :

1. **Balayage** (1,6 s) : un secteur lumineux (accent à 15 % d'opacité) fait un tour depuis 12 h.
2. **Glissement** (1,35 s, `smoothstep`) des points de l'ancienne position vers la nouvelle ; les nouveaux points apparaissent en fondu.
3. **Évaluations** : bot par bot (décalage 0,4 s, ordre des cartes), la ligne d'état affiche le compteur (« 40 scanned · 1 signal ») ; des **fils** sont tracés seulement pour les paires `FILTERED`, `REFUSED`, `ENTERED`, les positions ouvertes du bot et la paire sélectionnée (y compris `NO_SIGNAL`, qui s'éteint à l'entrée de la carte). Fil : Bézier en pointillés animés (6/5 px, 1,5 px, 72 % d'opacité), du point au bord gauche de la carte.
4. **Étapes** : l'impulsion allume les étapes une à une (1,1 s par étape) jusqu'au résultat ; une étape refusée passe en `stop` (`--hl-loss`) avec le motif dans la ligne d'état.
5. **Repos** : fils estompés en 1,7 s ; points, anneaux et lignes d'état restent jusqu'au cycle suivant.

- **Page d'accueil** : interrogation de `/public/radar` toutes les 20 s ; l'animation rejoue le dernier cycle connu à son arrivée (l'en-tête affiche l'heure du cycle, jamais « temps réel » continu).
- **Terminal** (mise en œuvre D.3 : interrogation toutes les 15 s, et rechargement immédiat sur les événements ci-dessous du bot sélectionné, seul bot dont le flux est ouvert) : déclenchée par les événements du flux temps réel : `SCAN_CYCLE_COMPLETED` (étapes 3–4 pour le bot concerné, sans attendre les autres), `ORDER_FILLED` (Execute), `POSITION_CLOSED` (Equity, anneau retiré) ; positions des points rechargées depuis `/market/radar` à chaque nouvelle minute (au plus une fois par minute) ou au changement de fenêtre.
- **Coalescence** : un nouveau cycle pendant une animation ⇒ l'état final du cycle précédent s'applique aussitôt, puis le nouveau cycle démarre. Plus de 6 fils simultanés ⇒ seuls les 6 plus récents sont tracés, le compteur reste exact.
- **Pause** : fige l'image ; **Replay** : rejoue le dernier cycle. Onglet masqué (`visibilitychange`) ⇒ boucle d'animation arrêtée.
- **`prefers-reduced-motion`** : ni balayage, ni glissement, ni pointillés animés ; l'état final s'affiche directement (fils pleins statiques pour les signaux et la sélection), lignes d'état identiques.

## Rendu et accessibilité

- Deux `canvas` (figure, fils), densité de pixels plafonnée à 2, redimensionnement par `ResizeObserver` ; ≈ 60 images/s pendant une animation, aucune image au repos.
- Figure : `role="img"` et `aria-label` décrivant les axes ; le sélecteur « Inspect pair » donne toutes les valeurs au clavier ; un clic sur un point (rayon de 17 px) le sélectionne ; les cartes de bots sont du texte (lisibles sans la figure).
- Mobile (< 740 px) : cartes sous la figure, figure de 300 px de haut, fils masqués (étapes et lignes d'état conservées), noms seulement pour BTC, la sélection et les 3 paires les plus éloignées du centre.
- États : `loading` (axes seuls), `stale` (cycle de plus de 5 min ou BTC indisponible : bandeau « Market data delayed »), `error` (« Radar unavailable », réessai 20 s), `empty` (aucun bot actif : la figure reste, cartes remplacées par « No bot running »).

## Tests

| ID | Vérification |
|---|---|
| T-RAD-01 | calcul : performance, `excess_pp`, `vol_ratio`, `y` sur des bougies connues ; report de clôture sur minute vide ; paire non jugeable sous W/3 ; paire sans données omise et comptée ; BTC absent ⇒ `stale` |
| T-RAD-02 | `SCAN_CYCLE_COMPLETED.outcomes` : NO_SIGNAL, UNKNOWN, COOLDOWN, FILTERED (motif), REFUSED (motif), ENTERED sur un cycle simulé ; seules les paires de l'univers du bot |
| T-RAD-03 | `/public/radar` sans jeton, fenêtre 1h seule, aucun champ sensible (montant absolu, identifiant, utilisateur) ; cache 15 s ; `/market/radar` exige un rôle de lecture et accepte 1h/4h/24h |
| T-RAD-04 | composant : points placés selon x/y et échelles fixes, plaquage au bord, lecture exacte au sélecteur, fils seulement pour signaux, positions et sélection, étape `stop` avec motif |
| T-RAD-05 | `prefers-reduced-motion` : aucune boucle d'animation, état final affiché ; Pause/Replay ; onglet masqué ⇒ boucle arrêtée |
