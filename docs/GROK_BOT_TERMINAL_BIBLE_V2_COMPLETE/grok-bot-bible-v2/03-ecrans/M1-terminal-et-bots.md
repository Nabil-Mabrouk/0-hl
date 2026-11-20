# Écrans M1.5 — Live Terminal (UI-01) et Bots & Sessions (UI-02)

> **0-hl — Bible V2.1 | 18 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Ce chapitre détaille le lot **M1.5** ([13-roadmap](../13-roadmap/01-plan-execution.md)) et précise [UI-01](UI-01-live-terminal.md), [UI-02](UI-02-bots-sessions.md), le [design system](../02-design/03-design-system.md) et les [animations](../02-design/04-animation-interactions.md) pour 0-hl. Il ne consomme que l'API M1.4 ([10-backend/04](../10-backend/04-api-m1.md)). En cas d'écart, **ce chapitre prévaut** pour l'implémentation M1.

## Intégration au châssis (ADR-015)

- Code dans **de nouveaux fichiers** : `frontend/src/features/terminal/`, `frontend/src/features/bots/`, `frontend/src/features/shared/` (client API/WS, formatage, garde de rôle) et `frontend/src/domain/routes.tsx`.
- `frontend/src/domain/routes.tsx` exporte `domainRoutes` : un fragment React de `<Route>` (`terminal`, `bots`), relatifs au préfixe de l'`AppShell` du châssis. Il sera monté par `App.tsx` dès que le template gitsky fournira ce point d'extension (ADR-015) ; d'ici là, les écrans sont développés et testés mais **non routés**.
- `Navbar.tsx` (fichier du projet) affiche « Terminal » et « Bots » à un utilisateur connecté.
- Les styles du terminal vivent dans `frontend/src/features/terminal/terminal.css` (tokens de 02-design/03, préfixe de classes `hl-`), sans toucher `theme.css`.

## Accès et états communs

- Utilisateur non connecté ⇒ redirection `/login`. Rôle métier vérifié par `GET /api/v1/me` ; `403` ⇒ état **forbidden** (message « rôle métier requis », aucun secret).
- États normalisés (02-design/04) : `loading` (squelette, aucune valeur financière), `ready`, `stale` (bandeau orange : worker arrêté, flux non `LIVE` ou WebSocket en reconnexion, avec l'âge), `error` (code + `correlation_id` de la réponse RFC 9457, bouton réessayer), `forbidden`.
- **Aucune valeur inventée** : tout champ `null` s'affiche `—` ; montants affichés depuis les chaînes décimales de l'API (formatage sans passage par un flottant pour l'arrondi d'affichage des montants : arrondi décimal sur chaîne).

## Client temps réel

- `GET /api/v1/terminal/snapshot` puis WebSocket `/ws/v1/terminal` (URL dérivée de `VITE_API_URL` : `http→ws`, `https→wss`) avec `hello {token, bot_id, last_position}`.
- Chaque événement reçu est ajouté au journal local (200 derniers) ; un événement d'état (`ORDER_*`, `POSITION_*`, `EQUITY_UPDATED`, `BOT_*`, `COMMAND_*`, `SCAN_CYCLE_COMPLETED`, `RECONCILIATION_*`, `DATA_*`, `PROVIDER_*`) déclenche un **rechargement de l'instantané** regroupé (au plus une fois par seconde) : l'UI n'interpole aucun état (02-design/04 « les animations traduisent un événement, n'en sont jamais la source »).
- Fermeture `4401` ⇒ rafraîchissement du jeton (`/api/auth/refresh`) puis reconnexion avec `last_position` ; autre fermeture ⇒ reconnexion avec backoff 1, 2, 4… 30 s ; `snapshot` reçu ⇒ remplacement complet de l'état. Indicateur de connexion : `LIVE`, `RECONNECTING`, `OFFLINE`.

## UI-01 Live Terminal

**Vue unique : pleine largeur (ADR-016, ADR-019).** Grille responsive dans le langage visuel de la capture 3 :
- **barre de tête** pleine largeur : bouton pause, « **0-hl** Terminal » + version, statut et uptime, puce d'activité, badge environnement/connexion ; à droite balance (grand), trades, wins, losses ;
- **colonne gauche** (≈ 300 px) : six cartes agents + YOU (clic ⇒ tiroir) ;
- **centre** : flux (candidats, stations RUG CHECK et EXECUTOR, mini-cartes FILTERED / SIZER / LOGGER / EQUITY), puis deux cartes côte à côte TRADE et RESULT ;
- **colonne droite** (≈ 380 px) : `$ tail -f night.log` sur toute la hauteur ;
- **bas** pleine largeur : tableau LOG ; bandeau sombre de la dernière ligne.
- Lisibilité : texte courant 13 px, libellés 11 px en capitales espacées, chiffres clés 24 à 32 px en chiffres tabulaires.
- Responsive : sous 1200 px la colonne droite passe sous le centre ; sous 800 px une seule colonne.

**Corrections après revue opérateur (2026-11-19) :**
- **Bandeau de provenance** permanent pour un bot `PAPER` : « PAPER — real Kraken prices · simulated execution · fictitious capital <capital déposé> ».
- **Bandeau d'entrées bloquées** dès qu'un blocage s'applique (liste `entry_blocks` de l'instantané) : perte du jour ≥ limite (avec valeur, limite et levée à 00:00 UTC), drawdown ≥ limite, kill switch, nombre maximal de positions atteint, bot non `RUNNING`.
- **Refus attribué à la bonne étape** : un `RISK_REJECTED` d'étape `RUG_CHECK` est une ligne `FILTERED` (carte RUG CHECK) ; d'étape `SIZER`, une ligne `REFUSED` (carte SIZER, avec le motif). La carte RUG CHECK n'affiche que ses propres rejets.
- **Mini-carte « NO SIGNAL »** (et non « FILTERED ») : paires évaluées au dernier scan qui ne remplissent pas les conditions de la stratégie, plus le nombre d'inconnues.
- **Taille et résultat d'un trade** calculés sur le **montant engagé à l'entrée** (`entry_notional` de l'instantané) : taille en % du capital déposé, résultat d'un trade clos en % de ce montant (jamais sur le coût restant, nul après la vente).
- **Symbole toujours résolu** (2026-11-20) : depuis `instrument_id` du payload, à défaut depuis la position de l'instantané dont l'identifiant est l'agrégat de l'événement, à défaut depuis le candidat de même `correlation_id` ; jamais `—` pour un événement de position.
- **Barre RESULT centrée sur le capital de départ** : gain vers la droite (vert), perte vers la gauche (rose) ; la demi-barre représente ±10 % du capital, au-delà la barre est pleine et la valeur exacte reste affichée.

**Contenu des zones** (hérité de la capture 3, contenu 0-hl, ADR-001/004) ; la réplique 550×688 qui reproduisait sa géométrie est retirée (ADR-019) :

| Zone (capture 3) | Donnée 0-hl |
|---|---|
| barre système : titre | `0-hl — <environnement> · six agents · log first` ; à droite l'identifiant de l'utilisateur, badge d'environnement (`PAPER`) et indicateur de connexion |
| bouton pause | `PAUSE_ENTRIES` / `RESUME_ENTRIES` selon le statut (Operator ; désactivé sinon) ; état « en attente » jusqu'à `COMMAND_COMPLETED`/`REJECTED` |
| titre + version | « **0-hl** Terminal » + badge `v<version de stratégie>` |
| statut + uptime | statut du bot en minuscules + uptime de session `hh:mm:ss` |
| puce d'activité | dernier événement EXECUTOR (`EXECUTOR · ENTERED $SYM`) sinon dernier scan (`SCANNER · checked N`) |
| balance / trades / wins / losses | `equity` (ou `—`), `stats.trades`, `stats.wins`, `stats.losses` |
| 6 cartes agents + YOU | statut `ACTIVE`/`IDLE`/`DOWN` (pastille), ligne descriptive fixe, ligne de détail issue des données (ci-dessous) ; clic ⇒ tiroir avec ses derniers événements |
| flux central | deux derniers candidats (cartes symbole + spike), stations RUG CHECK et EXECUTOR (dernier verdict), mini-cartes FILTERED (rejets du dernier scan), SIZER (taille en % d'equity de la dernière position), LOGGER (`line #position`), EQUITY (dernier PnL net clos) ; `checked N` = paires évaluées au dernier scan |
| TRADE · ENTRY vs EXIT | position ouverte la plus récente, sinon dernière clôture : symbole, `LONG`, variation (marque vs entrée ou PnL réalisé), entrée, sortie/`open`, stop, take-profit, taille en % d'equity, raison (features du candidat) |
| `$ tail -f night.log` | dernières lignes du journal local formatées (`HH:MM ENTER $SYM size 13%`, `FILTERED $SYM <critère>`…) |
| RESULT · start vs now | capital déposé (`owner_capital`) vs equity courante, barre de progression, `W / L` |
| LOG | dernières lignes ENTER / EXIT / FILTERED avec motif et valeur (taille ou PnL) ; clic ⇒ trace complète (`/events/trace/{correlation_id}`) |
| bandeau inférieur | dernière ligne du journal |

**Lignes de détail des agents** : SCANNER « flags only, never trades » + `last scan: N pairs · C cand.` ; RUG CHECK « nothing executes before a pass » + dernier verdict ; SIZER « never trades a fixed ticket » + `risk r% of equity` ; EXECUTOR « slippage cap s% » + dernier fill ; LOGGER « no trade without a reason » + `line #position` ; EQUITY « updates on every close » + equity et nombre de clôtures ; YOU « reads the log ».

**Animations** (sous-ensemble de 02-design/04) : arrivée d'une carte candidat (280 ms), flash de la carte SIZER (220 ms), anneau EXECUTOR au fill (250 ms), mise à jour des compteurs ; arrêt des pulsations quand le bot est en pause ; `prefers-reduced-motion` ⇒ aucune animation, statuts textuels inchangés.

**Complément d'instantané (backend)** : chaque position de l'instantané inclut `entry_features` (features clés du candidat : `volume_spike`, `buy_notional_share`, `window_notional_usd`) ; l'instantané inclut `config` (`strategy_version`, `max_trade_risk_pct`, `max_slippage_pct`) et `owner_capital`.

## UI-02 Bots & Sessions (minimal)

- Tableau : nom, environnement, statut, version de stratégie, uptime, lien « ouvrir le terminal ».
- Actions selon le statut (transitions de 10-backend/03) : `START` (READY), `PAUSE_ENTRIES` (RUNNING), `RESUME_ENTRIES` (PAUSED), `STOP` et `EMERGENCY_STOP` (RUNNING/PAUSED/DEGRADED). Boutons masqués pour un utilisateur sans rôle Operator (E-stop visible aussi pour Risk Officer) — confort uniquement, le backend décide.
- `STOP` et `EMERGENCY_STOP` exigent un **dialogue de confirmation** récapitulant bot, environnement, version de stratégie, effet attendu et conséquence sur les positions (E-stop : liquidation immédiate des positions).
- Chaque clic génère une `Idempotency-Key` (`crypto.randomUUID`) ; affichage `PENDING` puis `COMPLETED` ou `REJECTED` (code) par interrogation de `/api/v1/commands/{id}` toutes les secondes (max 30 s).

## Tests M1.5

| ID | Vérification |
|---|---|
| T-M1.5-01 | formatage : chaînes décimales, `—` pour `null`, uptime, pourcentages |
| T-M1.5-02 | client WS : `hello` avec `last_position`, snapshot remplace l'état, événements ajoutés, reconnexion avec la dernière position, `4401` ⇒ refresh puis reconnexion |
| T-M1.5-03 | terminal : rend les valeurs de l'instantané (equity, trades, wins/losses, agents, scan, position, log) ; `null` ⇒ `—` |
| T-M1.5-04 | terminal : bandeau `stale` si worker arrêté ou flux non `LIVE` ; état forbidden sur `403` ; erreur avec `correlation_id` |
| T-M1.5-05 | bouton pause : envoie `PAUSE_ENTRIES` avec `Idempotency-Key`, état en attente, puis statut |
| T-M1.5-06 | bots : actions par statut ; E-stop et STOP demandent confirmation ; aucune commande envoyée sans confirmation |
| T-M1.5-07 | `prefers-reduced-motion` : aucune classe d'animation |
| T-M1.5-08 | `npm run typecheck && npm run test && npm run build` verts |
| T-M1.5-10 | bandeaux provenance et entrées bloquées ; refus SIZER affiché `REFUSED` sur la carte SIZER ; taille et résultat sur le montant engagé ; « NO SIGNAL » ; barre RESULT centrée |
| T-M1.5-09 | vue pleine largeur unique, sans sélecteur de réplique (ADR-016, ADR-019) |

*La mesure de fidélité par capture Playwright 550×688 est abandonnée (ADR-019).*
