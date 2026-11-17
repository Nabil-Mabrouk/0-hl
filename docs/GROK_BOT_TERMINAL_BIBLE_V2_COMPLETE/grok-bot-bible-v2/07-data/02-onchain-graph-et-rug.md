# Analyse on-chain, graphes de wallets et anti-rug

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Modèle graphe

Nœuds `Wallet`, `Token`, `Pool`, `Contract`, `Venue`; arêtes `TRANSFER`, `SWAP`, `ADD_LIQUIDITY`, `REMOVE_LIQUIDITY`, `MINT`, `BURN`, `AUTHORITY_CHANGE`, enrichies d'heure, transaction, montant, unité, bloc et source. Un transfert on-chain n'est **pas** un trade. Les échanges internes CEX ne sont généralement pas visibles on-chain.

## Concentration

Pour un snapshot de holders, `top10_share = sum(token_balance_top10)/circulating_supply_estimate` (exclure adresses de burn/pool selon définition explicitée). Labels développeur, escrow, lockers et contrats sont probabilistes sauf preuve forte. Réserves fractionnelles / wrapped tokens sont à signaler.

## Bundled launches

Heuristique : wallets achetant proches d'un même bloc/lancement, financement commun, interactions corrélées ou forte top-N concentration. Score `bundle_risk` explicable par critères et seuils, jamais assertion de fraude automatique. Possibilité de faux positifs avec market makers, airdrops ou acteurs qui partagent une infrastructure.

## LP lock

Conserver le contrat de verrouillage, l'adresse, la quantité, la durée restante, les droits d'administration et les parts verrouillées. Ne jamais afficher `LP locked 95%` sans préciser dénominateur et horodatage. Un verrouillage ne protège pas contre honeypot, mint incontrôlé, blacklist ou externalités du pool.

## Score du risque

Plutôt qu'un pourcentage de fraude pseudo-scientifique, utiliser verdict `PASS`, `REVIEW`, `REJECT`, `UNKNOWN` avec facteurs et niveaux de confiance. Un nombre `74% risk` peut être affiché seulement s'il résulte d'un modèle calibré et validé ; sinon utiliser « 74/100 heuristic score » avec légende.

## Backtest on-chain

La simulation doit reconstruire **l'état connu au bloc décisionnel**, y compris déploiement, holders alors visibles, réserves, taxes et permissions ; interdit de profiter des renseignements révélés après le rug.
