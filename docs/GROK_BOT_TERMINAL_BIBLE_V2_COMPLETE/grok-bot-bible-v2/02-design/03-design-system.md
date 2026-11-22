# Design system, tokens et composants

> **0-hl — Bible V2.1 | 22 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

## Direction artistique (ADR-028)

**Sombre uniquement**, une seule couleur d'accent (vert citron), grands panneaux arrondis, texte en Inter et **chiffres en JetBrains Mono**. Référence : la maquette « Market radar » fournie par l'opérateur le 2026-11-22 (`temp/0-hl-market-radar.html`, non versionnée ; ses valeurs sont reprises ci-dessous). Le même langage s'applique à **toutes** les pages du projet : accueil, Terminal, Bots, Trades, Stats, Strategies et éditeur, Risk, AI, et aux pages du châssis qui lisent les variables de `landing.css` (Login, Apprendre). L'admin du châssis garde `admin-theme.css` (châssis, hors périmètre).

Remplace la direction claire, dense et monospace inspirée de la capture 3 (V2.0, ADR-016, ADR-019) : la capture 3 n'est plus une référence visuelle ; le **contenu** des zones du Terminal (M1-terminal-et-bots) reste valable.

## Tokens

Définis **une seule fois** dans `frontend/src/branding.local.css` (projet), sous `:root` ; `landing.css` (projet) reprend les variables lues par le châssis. Aucune couleur écrite en dur dans les feuilles des pages : toute valeur passe par un token.

```css
:root {
  color-scheme: dark;
  /* surfaces */
  --hl-bg: #101514;        --hl-panel: #171f1c;     --hl-panel-2: #1c2622;  /* carte, ligne survolée */
  --hl-control: #202b23;   --hl-control-line: #4d5e4f;
  --hl-line: #354239;      --hl-line-soft: #26302b;
  /* texte */
  --hl-text: #f1f4e8;      --hl-muted: #a9b8ad;     --hl-faint: #7d8c81;    /* aide, axes */
  /* accent et fonctionnel */
  --hl-accent: #dcf995;    --hl-accent-ink: #101514;                         /* texte sur accent */
  --hl-gain: #8be3a5;      --hl-loss: #ffac9b;      --hl-warn: #ffd08a;     --hl-info: #9bc8ff;
  /* stratégies (toujours accompagnées du nom) [OPEN] calibrage visuel */
  --hl-s-volume-spike: #ffd09b; --hl-s-breakout-1h: #9bc8ff; --hl-s-rebound: #d7baff;
  --hl-s-rotation: #8ee6dc;     --hl-s-pullback: #f9a8d4;    --hl-s-other: #c9d3c6;
  /* forme */
  --hl-radius-board: 16px; --hl-radius-card: 8px; --hl-radius-control: 5px;
  --hl-space-1: 4px; --hl-space-2: 8px; --hl-space-3: 12px; --hl-space-4: 16px; --hl-space-5: 24px; --hl-space-6: 32px; --hl-space-7: 48px;
  --hl-page-max: 1440px;   --hl-gutter: clamp(16px, 5vw, 72px); --hl-header-h: 68px;
  /* typographie */
  --hl-font: "Inter", system-ui, sans-serif;
  --hl-mono: "JetBrains Mono", ui-monospace, monospace;
  /* variables lues par le châssis (theme.css, landing.css, learn.css) */
  --color-primary: var(--hl-accent); --color-primary-foreground: var(--hl-accent-ink);
  --font-family-base: var(--hl-font); --font-family-display: var(--hl-font);
}
```

`landing.css` fixe `--bg: var(--hl-bg)`, `--surface: var(--hl-panel)`, `--text: var(--hl-text)`, `--border: var(--hl-line)`, `--nav-bg: rgba(16, 21, 20, .92)` sans bloc clair (le bloc `prefers-color-scheme: dark` actuel devient la seule valeur).

**Couleur d'une stratégie** : `--hl-s-<strategy_id>` ; identifiant inconnu ⇒ `--hl-s-other`. Le nom de la stratégie est toujours écrit à côté de sa couleur (jamais la couleur seule).

**Gain et perte** : `--hl-gain` / `--hl-loss`, **toujours avec le signe** (`+12.40` / `−3.10`) ; l'accent citron n'indique jamais un gain.

## Typographie

- Polices chargées pour tout le site par la Navbar du projet (présente sur chaque page) : un seul lien Google Fonts injecté dans `<head>` (Inter 400, 500, 600, 700, 800 et JetBrains Mono 400, 500, `display=swap`) ; un `@import` distant dans un fichier CSS importé serait placé après d'autres règles une fois les feuilles regroupées, donc ignoré. La page d'accueil garde en plus son chargement de polices du châssis (test `Landing.fonts`).
- **Chiffres** (prix, montants, %, compteurs, heures, identifiants) : `--hl-mono`, `font-variant-numeric: tabular-nums` ; largeur stable au tick. Valeur indisponible : `—`, jamais `0` ; `UNKNOWN` et `STALE` sont des états écrits.
- Échelle : corps 14 px (écrans de travail) / 16 px (accueil, Apprendre) ; libellés 11 px en capitales espacées (`letter-spacing: .06em`, `--hl-muted`) ; titres de panneau 16 px 600 ; chiffres clés 24–32 px ; titre de l'accueil `clamp(2.75rem, 6.2vw, 6.5rem)`, interligne .95, `letter-spacing: -.075em`, 800, seconde ligne en accent.
- Marque : « ◉ 0-hl », 1.5rem, 800, `letter-spacing: -.06em`, le « ◉ » en accent.

## Mise en page

- **En-tête** commun (Navbar du projet), hauteur `--hl-header-h`, bordure basse `--hl-line`, contenu centré sur `--hl-page-max` avec `--hl-gutter` :
  - marque à gauche ;
  - navigation regroupée : **Trading** (Terminal, Bots, Trades, Stats), **Lab** (Strategies, Risk), **AI**, **Learn** ; Admin (rôle `admin`) ; langue (si le module i18n est actif) ; Log in / Log out ;
  - page en cours soulignée en accent (`aria-current="page"`) ; liens du routeur (`NavLink`), plus de rechargement complet ;
  - sous 900 px : bouton « Menu » (cible ≥ 44×44) ouvrant un panneau plein écran ; Échap ferme.
- **Pied de page** (Footer du projet) dans le même langage.
- **Page** : `main` centré sur `--hl-page-max`, marges `--hl-gutter`.
- **Board** (panneau principal) : bordure `--hl-line`, rayon `--hl-radius-board`, fond `--hl-panel` (dégradé radial léger autorisé sur le board du radar seulement), barre d'outils en haut et pied facultatif, séparés par `--hl-line`.
- **Carte** : fond `--hl-panel-2`, bordure `--hl-line`, rayon `--hl-radius-card`, **liseré gauche de 3 px** à la couleur de la stratégie pour une carte de bot.
- Grilles des écrans de travail : 12 colonnes, gouttière `--hl-space-4` ; sous 1200 px les colonnes latérales passent sous le centre ; sous 740 px une seule colonne.

## Composants partagés

`frontend/src/features/shared/ui/` (projet) ; chaque page les utilise au lieu de ses propres styles.

| Composant | Rôle |
|---|---|
| `Board` | panneau principal : titre, sous-titre `small`, actions de barre d'outils, pied |
| `Card` | carte, avec `accent` facultatif (liseré gauche) |
| `BotCard` | nom du bot, stratégie (couleur + nom), statut écrit, ligne d'état, bandeau d'étapes Scan → Check → Size → Execute → Equity (états `on`, `stop`) — utilisé par le radar, le Terminal, Bots |
| `StatusBadge` | statut écrit + pastille (RUNNING, PAUSED, READY, STOPPED, LIVE, STALE, UNKNOWN, SUCCEEDED, FAILED…) |
| `Money`, `Pct`, `Num` | chiffres mono tabulaires, signe, couleur gain/perte, `—` si nul |
| `DataTable` | tableau dense : en-têtes en libellés capitales, lignes séparées `--hl-line-soft`, survol `--hl-panel-2`, nombres alignés à droite |
| `Button` | `primary` (fond accent, texte `--hl-accent-ink`), `secondary` (fond `--hl-control`, bordure `--hl-control-line`), `danger` (texte `--hl-loss`) ; désactivé lisible |
| `Field`, `Select`, `Textarea` | contrôles `--hl-control`, rayon `--hl-radius-control` |
| `Dialog` | fenêtre modale, piège du focus, Échap |
| `Tabs` | onglets soulignés en accent |
| `EmptyState`, `ErrorState`, `Banner` | états `empty`, `error`, bandeaux (PAPER, ENTRIES PAUSED, budget IA…) |

## Accessibilité

Contraste AA au moins (texte `--hl-text` et `--hl-muted` sur `--hl-panel` et `--hl-panel-2`, accent sur fond sombre, texte `--hl-accent-ink` sur accent) ; états jamais portés par la seule couleur (texte, signe, icône) ; **focus visible** `outline: 2px solid var(--hl-accent); outline-offset: 3px` ; navigation clavier complète ; `prefers-reduced-motion` respecté partout (02-design/04) ; alternative textuelle des figures animées (02-design/05) ; cibles ≥ 32×32 sur bureau et ≥ 44×44 sur mobile ; aucun contrôle critique caché au survol seul.

## Mesures de fidélité

Aucune mesure pixel (ADR-019). La maquette « Market radar » est une **direction** : tokens, proportions et comportements ci-dessus font foi.
