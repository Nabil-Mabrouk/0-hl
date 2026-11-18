# Six agents — orchestration et responsabilités

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Principe

Les six « agents » visibles sont des **rôles métier**. Leur implémentation peut être un module FastAPI / worker et ne nécessite pas un LLM. Des sous-agents IA explicatifs peuvent exister mais ne signent aucune autorisation d'ordre.

| Agent | Entrée | Sortie | Délai SLO illustratif | Faute par défaut |
|---|---|---|---|---|
| SCANNER | tokens, pools, OHLCV, trades | Candidate | P95 < 2s après donnée | SKIP ou RETRY |
| RUG CHECK | CEX : statut paire, carnet, ticker, règles d'ordre — DEX : contrat, LP, holders, wallets | RiskAssessment | P95 < 5s hors provider | REJECT UNKNOWN |
| SIZER | capital, stop, liquidité, policy | PositionProposal | P95 < 250ms | BLOCK |
| EXECUTOR | OrderIntent approuvé, venue | Order/Fill | dépend venue | UNKNOWN+RECONCILE |
| LOGGER | tous événements | log append-only | P95 < 500ms | buffer durable |
| EQUITY | fills, ledger, marks | portfolio snapshot | P95 < 1s | STALE+alert |

## SCANNER : algorithme

1. Recevoir event `POOL_DISCOVERED` ou tick normalisé ; vérifier chain/identité et timestamp.
2. Enrichir avec métriques requises et TTL ; si fenêtre insuffisante, `PENDING_DATA`.
3. Pour chaque stratégie active sur cet univers, calculer signal et score explicable ; déposer Candidate unique `(pool_id,strategy_version,window_start)`.
4. Dédoublonner signaux concurrents ; émettre `CANDIDATE_DETECTED` ou `CANDIDATE_SKIPPED`.

## RUG CHECK : algorithme (variante DEX — hors périmètre tant qu'aucune venue DEX, ADR-004)

1. Charger version anti-rug, token contract et provenance.
2. Vérifier taxes, transfert/vente possible, concentration, LP lock, niveau développeur et cluster heuristics.
3. Pour chaque critère : `PASS/FAIL/UNKNOWN`, valeur, unité, source, horodatage, justificatif.
4. Politique `FAIL => REJECT`, `UNKNOWN sur critère bloquant => REJECT`, `PASS => RISK_PASSED`.
5. Toute revalidation suite à changement de contrat/liquidité produit un nouveau assessment, sans modifier rétroactivement le précédent.

## RUG CHECK — variante CEX (ADR-004)

Implémentation active pour Kraken Spot. Libellé visuel inchangé (`RUG CHECK`) dans le terminal ; libellé fonctionnel « market-quality gate ». Chaque critère produit `PASS/FAIL/UNKNOWN`, valeur, unité, source, `observed_at`, justificatif. Politique : un `FAIL` ⇒ `REJECT` ; un `UNKNOWN` sur critère bloquant ⇒ `REJECT` ; tous `PASS` ⇒ `RISK_PASSED`. Seuils = paramètres de la `RiskPolicy` (valeurs de départ ci-dessous).

| Critère | Règle de départ | Bloquant |
|---|---|---|
| `pair_status` | statut Kraken = `online` (refus si `cancel_only`, `post_only`, `limit_only`, `reduce_only`, `delisted`, inconnu) | Oui |
| `data_freshness` | état du flux `LIVE` pour la paire : connexion vivante, snapshot reçu, dernier checksum valide (07-data/03) | Oui |
| `spread` | `spread_bps ≤ max_spread_bps` (30) | Oui |
| `entry_depth` | coût simulé d'achat de la taille proposée par consommation du carnet : glissement ≤ `max_slippage_pct` (0,5 %) | Oui |
| `exit_depth` | vente simulée de la même quantité sur le carnet bid actuel : glissement ≤ `max_slippage_pct` ; sinon taille réduite par le SIZER ou rejet | Oui |
| `order_minimums` | quantité ≥ `ordermin` et notionnel ≥ `costmin` de la paire, après arrondi aux `lot_decimals`/`tick_size` | Oui |
| `abnormal_move` | `|price_change_5m| ≤ max_abnormal_move_pct` (15 %) — au-delà, présomption d'anomalie | Oui |
| `volume_floor` | volume 24h USD ≥ `min_volume_24h_usd` | Oui |
| `cooldown` | pas de sortie sur la même paire depuis < `cooldown_seconds` | Oui |

Les messages du terminal reprennent la forme de la capture (`FILTERED — reason: spread 42 bps`, `passed $SOL`), sans mention de LP/dev wallet sur une venue CEX.

## SIZER : algorithme

1. Charger ledger et positions à jour ; contrôler politique globale et limites par bot.
2. Vérifier stop initial, quantization, liquidité et coût de sortie pessimiste.
3. Calculer notional selon formule de risque ; éviter exposition corrélée et double intention.
4. Produire proposition avec `input_hash`, `limits_applied`, `rejection_reasons`.

## EXECUTOR : algorithme

1. Vérifier OrderIntent approuvé et non expiré, policy effective, cash disponible et market quote actuelle.
2. Réserver capital ; générer idempotency key ; soumettre via adaptateur exchange/DEX.
3. Réconcilier ACK, fill, partial, reject ou unknown ; aucun double envoi sans introspection d'ordre.
4. Reporter fills au ledger ; surveiller sorties et confirmation effective.
5. Toute erreur impossible à résoudre déclenche blocage d'entrées et alerte.

## Exécution sur le châssis (ADR-007)

Les six agents tournent dans le service `worker` du châssis, au sein de `run_cycle` exécuté en boucle continue. Chaque agent est une étape idempotente qui consomme les événements de l'outbox PostgreSQL au-delà de son curseur persistant, produit ses événements dans la même transaction que ses écritures métier, puis avance son curseur. `stop_requested` est vérifié entre chaque étape ; un arrêt en cours d'étape annule la transaction, sans effet partiel. Le flux de marché Kraken (WebSocket) est maintenu par un ingesteur dans le même processus, qui écrit les observations normalisées et met à jour la fraîcheur.

## LOGGER : algorithme

Écrire événements sur stockage append-only (inbox/outbox), indexer par event_id, bot_id, correlation_id, epoch, token_id, actor et type ; fournir recherche, export avec intégrité et contrôle de rétention. Aucun ordre ne dépend d'une animation ou d'un composant de log UI.

## EQUITY : algorithme

Consommer ledger, prix marqués et confirmations; calculer balances/positions/equity et émettre snapshots périodiques, y compris `UNKNOWN` en cas de données manquantes. Réconciliation venue toutes les X minutes et après événements critiques ; divergence bloque de nouvelles entrées jusqu'à résolution.
