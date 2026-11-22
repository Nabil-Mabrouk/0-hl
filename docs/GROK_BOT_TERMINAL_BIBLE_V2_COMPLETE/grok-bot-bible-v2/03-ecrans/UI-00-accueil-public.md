# UI-00 — Page d'accueil publique (0-hl.com)

> **0-hl — Bible V2.1 | 20 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer ; `[ADR-xxx]` décision actée.

Objectif : une page d'accueil moderne qui dit en une phrase ce qu'est 0-hl, montre que le système **tourne vraiment** (chiffres en direct) et mène au Terminal. Demande de l'opérateur du 2026-11-20 : « afficher le nombre de bots en marche et d'autres statistiques, comme le nombre de trades ».

## Intégration au châssis

- `frontend/src/pages/Landing.tsx` et `landing.css` appartiennent au projet (AGENTS.md « À vous ») ; Landing.tsx garde la Navbar, le Footer, le chargement des polices et les blocs du manifeste **autres que `hero`** (la liste d'attente `email_capture` reste en bas de page).
- Les sections 0-hl sont un composant du projet (`frontend/src/features/landing/`), rendu à la place du bloc `hero` du manifeste.

## Contenu

1. **Hero** (ADR-028, style 02-design/03) : titre sur deux lignes « One market. / Different decisions. » (seconde ligne en accent), sous-titre « See how paper trading agents read the same market and follow a signal from scan to equity. », boutons *Open the terminal* (`/terminal`, ou `/login` si non connecté) et *How it works* (ancre) ; badge permanent « PAPER — real Kraken prices · simulated execution ».
1 bis. **Market radar** ([02-design/05](../02-design/05-market-radar.md)) pleine largeur sous le hero, fenêtre 1h, données de `GET /api/v1/public/radar` (interrogé toutes les 20 s) : figure à gauche, cartes des bots actifs à droite (stratégie, statut, résultat net en %, étapes), Pause / Replay, sélecteur « Inspect pair ». Remplace le pipeline animé.
2. **Chiffres en direct** (pastille « LIVE » si le worker est vivant, « OFFLINE » sinon) : bots en marche / bots au total, trades exécutés (total), trades sur 24 h, positions ouvertes, paires suivies en direct / abonnées, évaluations de marché sur 24 h, jours de données de marché enregistrés, dernier trade (il y a …). Rafraîchis toutes les 30 s ; compteur animé à l'arrivée ; valeur inconnue ⇒ « — », jamais 0.
3. **Comment ça marche** : cinq étapes (Scan, Gate, Size, Execute, Measure), une phrase chacune.
4. **Le laboratoire** : versions immuables, backtest qui rejoue le vrai moteur, frais comptés (≈ 0,9 % par aller-retour), plusieurs bots comparés côte à côte.
5. **Transparence** : PAPER uniquement, aucun ordre réel, aucune clé de retrait, pas un conseil en investissement.
6. Blocs du manifeste restants (liste d'attente), Footer.

**Décision (2026-11-20, levée par ADR-028 le 2026-11-22)** : la page publique peut montrer les résultats des bots paper (résultat net en %, jamais de montant absolu). `/public/stats` reste un agrégat ; les détails par bot passent par `/public/radar`.

## API publique

`GET /api/v1/public/stats` — **sans authentification**, agrégats uniquement (aucun identifiant de bot, nom, montant, stratégie ou paire détenue), mis en cache 30 s côté serveur :

`bots_total`, `bots_running`, `trades_total`, `trades_24h`, `open_positions`, `pairs_live`, `pairs_subscribed`, `worker_alive`, `evaluations_24h`, `market_data_days`, `last_trade_at`, `generated_at`. Valeur incalculable ⇒ `null`.

## Tests

| ID | Vérification |
|---|---|
| T-UI00-01 | l'endpoint répond sans jeton ; chiffres exacts sur un jeu connu ; aucun champ sensible (noms, montants, identifiants) ; cache de 30 s |
| T-UI00-02 | la page affiche les chiffres de l'endpoint, « LIVE »/« OFFLINE », « — » si l'appel échoue ; boutons vers `/terminal` ou `/login` selon la connexion |
| T-UI00-03 | blocs du manifeste autres que `hero` toujours rendus (liste d'attente) |
| T-UI00-04 | le radar est rendu sous le hero avec les données de `/public/radar` ; état `error` si l'appel échoue, la page reste utilisable (T-RAD-01..05 dans 02-design/05) |
