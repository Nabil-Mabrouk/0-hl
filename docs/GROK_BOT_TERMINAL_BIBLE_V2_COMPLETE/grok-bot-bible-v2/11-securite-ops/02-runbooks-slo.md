# Exploitation 24/7, incidents et observabilité

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Métriques et SLO

Mesurer end-to-end signal-latency, provider freshness, queue lag, agent processing p95, order submit outcome, unknown-order count, reconciliation mismatches, websocket gap, CPU/memory, DB replication, user session 401/403, number of blocked trades. Dashboard opérateur séparé des profits.

## Runbook A — API marché indisponible

Détecter dépassement TTL; ouvrir alerte; basculer source alternative si sémantique compatible; interdire nouvelles entrées dépendantes; laisser exits confiés à watchdog; rétablir après N échantillons frais et approbation si besoin. Documenter impact et intervalle.

## Runbook B — Ordre de statut inconnu

Geler intention, interroger venue par `client_order_id`/tx hash/nonce, vérifier fills/balances, réconcilier ledger, relancer seulement si ordre définitivement non exécuté, consigner l'analyse. Ne jamais cliquer « retry » sans état distant validé.

## Runbook C — Ledger divergant

Déclencher `RECONCILIATION_REQUIRED`, bloquer nouvelles entrées, exporter relevés de fills et mouvements, isoler les différences de devises/gas, produire écriture compensatrice auditée si nécessaire et refaire comparaison.

## Runbook D — Panne du moteur d'exécution

Suspendre entrées, préserver sortie sur venue si ordres protecteurs disponibles; redémarrer avec inbox/outbox et reprendre contrôle des positions. Recréer état depuis dernier snapshot + événements vérifiés, sans double exécution. Informer l'opérateur.

## Runbook E — Kill switch

`BLOCK_NEW_ENTRIES` immédiat, annulation best-effort des ordres non exécutés si politique le prévoit, surveillance continue de positions et instructions de liquidation séparées. Ne jamais assimiler kill switch à « tuer le processus ». Journaliser autorisation et résultat de chaque action.

## Maintenance

Sonde readiness indépendante de liveness ; arrêt gracieux des workers ; migration DB réversible ou expand/contract ; backups chiffrés et restauration testée ; chaos tests et postmortem non accusatoire, avec actions correctrices mesurables.
