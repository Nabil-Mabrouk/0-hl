# Agents IA, outils et explicabilité

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Architecture hybride

Séparer `Decision Core` déterministe (sources validées, calculs, risque, exécution) de `Research Assistant` optionnel (résumés, classification d'anomalies, rédaction d'explications, comparaison d'hypothèses). Les modèles IA peuvent être sélectionnés (OpenAI, Groq, xAI, local) par adaptateur, coût, latence et version ; leurs suggestions ne doivent pas devenir ordre directement.

Mise en œuvre M2 : vocabulaire, principes et budget dans ADR-026, outils, agents, services et journal dans [03-ecrans/UI-16](../03-ecrans/UI-16-services-ia.md).

## Tool interface

Outils lecture seule par défaut : `get_candidate`, `get_market_features`, `get_risk_assessment`, `get_portfolio_snapshot`, `get_trade_trace`, `get_backtest_report`, `get_provider_status`. Les outils mutateurs passent exclusivement par des API RBAC distinctes et une approbation utilisateur explicite.

## Prompt & modèle

Versionner `prompt_id`, `prompt_sha256`, `model_id`, paramètres, outil invoqué, sortie JSON schema validée, timestamp, source references, coût et latence. Ne pas laisser un modèle calculer seul des montants monétaires sans validation code. Détecter injection de prompts via sources non fiables (description token, nom de contrat, tweet).

## Évaluations

Banc de cas : token sain, honeypot, bundle soupçonné, rug avec données contradictoires, source périmée, variation de prix extrême, faux signal, ordre rejeté. Mesurer fidélité de l'explication aux données, hallucinations de source, capacité à dire UNKNOWN, temps de réponse et coût. Un mauvais score désactive l'agent explicatif sans arrêter la protection deterministic.

## Gouvernance

Traitement local ou contrôlé des clés/identifiants, aucune transmission de secrets aux LLM, autorisations minimales, logs d'accès redacted. La décision humaine d'activation LIVE reste explicite et auditée.
