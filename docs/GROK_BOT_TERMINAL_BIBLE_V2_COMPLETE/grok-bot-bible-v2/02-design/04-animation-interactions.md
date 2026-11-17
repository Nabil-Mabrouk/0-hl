# Animations, états et interactions

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Modèle événement → animation

Les animations traduisent la réception d'un événement métier et n'en sont jamais la source. Les contrôles administratifs produisent des commandes vérifiées côté serveur ; l'UI attend la confirmation avant d'afficher le changement d'état définitif.

| Événement | Mouvement | Temps cible | Alternative reduced-motion |
|---|---|---:|---|
| `CANDIDATE_DETECTED` | Carte qui arrive de SCANNER | 280 ms | Apparition sans translation |
| `RISK_STARTED` | Halo du nœud check | 850 ms en boucle | Badge `CHECKING` |
| `RISK_REJECTED` | Passage rose + tag FILTERED | 350 ms | Tag instantané |
| `RISK_PASSED` | Particule vers SIZER | 450 ms | Statut PASS |
| `POSITION_SIZED` | Flash violet + mise à jour | 220 ms | Texte mis à jour |
| `ORDER_SUBMITTED` | Tracé vers EXECUTOR | 350 ms | Badge SUBMITTED |
| `ORDER_FILLED` | Anneau vert + trade card | 250 ms | Trade card ouverte |
| `POSITION_CLOSED` | Compteur et log | 380 ms | Mise à jour directe |
| `BOT_PAUSED` | Stop des pulses de scanner | immédiat | Texte PAUSED |

## Politique de coalescence

Le flux d'événements est intégralement persisté ; l'UI peut regrouper les mises à jour de prix à 2–5 Hz sans perdre les événements de décision. Les changements de solde ne sont pas interpolés au-delà des valeurs reçues (ne pas afficher de faux gains intermédiaires). Les événements de sécurité ne sont jamais dédoublonnés dans le journal, seulement regroupés en notification.

## États UX normalisés

Chaque écran doit avoir `loading`, `empty`, `ready`, `stale`, `error`, `forbidden` et `maintenance`. Un formulaire doit avoir `clean`, `dirty`, `invalid`, `submitting`, `saved`, `conflict`. Une édition concurrente utilise `ETag`/version et refuse un écrasement silencieux. Les opérations sensibles nécessitent un dialogue récapitulant l'instance, le compte, la version de stratégie, le mode, l'effet attendu et les conséquences sur les positions existantes.

## Interactions terminal

Clic agent → drawer détaillé avec dernière activité, statut, entrées/sorties, erreurs. Clic token → fiche token et analyses vérifiables. Clic trade → timeline signal, risk, sizing, ordre, fills et sortie. Clic log → panneau d'événement et trace corrélée. Bouton Pause → commande `PAUSE_ENTRIES`; la supervision des positions continue. Bouton E-stop → arrêt des nouvelles entrées et application d'une politique de liquidation configurable, jamais suppression arbitraire des ordres.
