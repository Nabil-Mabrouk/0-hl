# Sécurité applicative, droits, secrets et conformité

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Principes

Sécurité inspirée d'OWASP ASVS 5.0 ; authentification, RBAC+ABAC, CSRF/SSRF, protection injection, validation DTO stricte, rate limiting, TLS, audits et provenance des dépendances. Les permissions de l'interface ne remplacent jamais la vérification backend. La gestion des clés est le risque critique.

## Secrets et comptes

Clés externalisées dans secret manager ; chiffrement au repos et en transit ; rotation ; aucun secret exposé en frontend ou log. Environnements paper/live séparés, sous-comptes trading sans capacité de retrait quand le fournisseur le permet. Clés blockchain privées conservées dans KMS/HSM/signing service séparé si applicable ; transactions approuvées suivant policy.

## Approvals

Transition vers live exige MFA fort et approbation de deux rôles distincts si organisation multi-opérateurs; en solo, confirmation explicite, délai de révision et audit obligatoire, sans feindre une seconde personne. Abaissement des limites et modification du compte d'exécution sont commandes sensibles horodatées ; rôles stricts.

## Risque juridique et commercial

Usage individuel propre vs service commercial à des tiers : exigences réglementaires et fiscales différentes selon juridiction, service et actifs. Avant LIVE, revue juridique applicable et vérification des conditions d'API des plateformes. Aucune fonctionnalité ne doit promettre une stratégie rentable ou couvrir la manipulation de marché.

## Threat model

Menaces : vol d'API keys, prompt injection via contenu de token, attaque supply-chain, double order après restart, faux fournisseur de prix, time skew, données oracle manipulées, logique front contournée, replay d'une commande, perte du bus, évitement des risk limits, admin compromis. Pour chaque menace : barrière prévention, détection, runbook, test d'attaque.

## Kraken (ADR-009)

Premier jalon : **aucune clé privée Kraken**, uniquement les API publiques. Pour le LIVE futur : clé API dédiée à 0-hl, permissions limitées à « Query Funds », « Query Open Orders & Trades », « Query Closed Orders & Trades », « Create & Modify Orders », « Cancel/Close Orders » ; **jamais** « Withdraw Funds » ni gestion des clés ; liste blanche d'IP si l'infrastructure le permet ; stockage hors dépôt et hors `.env` versionné, référencé par `CredentialRef` ; rotation journalisée. Vérifier les conditions d'utilisation de l'API Kraken et la réglementation applicable à l'usage personnel avant l'ouverture du LIVE.
