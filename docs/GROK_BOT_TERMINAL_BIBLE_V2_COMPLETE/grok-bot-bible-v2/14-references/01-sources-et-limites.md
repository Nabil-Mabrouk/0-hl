# Sources, hypothèses et limitations

> **0-hl — Bible V2.1 | 17 novembre 2026 | Statut : spécification de référence (source unique de vérité)**
> **Traçabilité :** `[OBS]` vu dans les captures ; `[INF]` inféré ; `[SPEC]` à construire ; `[OPEN]` à arbitrer.

## Corpus observé

- `../assets/captures/capture_01.png` — terminal à 2h45, capital 1 344,98 $, RIZZO.
- `../assets/captures/capture_02.png` — terminal à 4h23, capital 3 362,91 $, CHIRPO.
- `../assets/captures/capture_03_canonique.png` — terminal à 6h38, capital 10 745,21 $, GRUBZ ; référence graphique.
- `../assets/captures/prototype_v1.png` — rendu du premier prototype, non équivalent au logiciel original.
- `../original/BIBLE_V1.md` — spécification historique dont les hypothèses sont conservées et développées dans la V2.

## Documentation publique de référence (vérifiée novembre 2026)

- Kraken API (REST Spot, WebSocket v2) : https://docs.kraken.com/api/
- Kraken — permissions des clés API et frais Spot : centre d'aide Kraken (à vérifier avant ADR LIVE)
- DEX Screener API : https://docs.dexscreener.com/api/reference
- GeckoTerminal API V2 : https://api.geckoterminal.com/docs/index.html
- Freqtrade backtesting : https://docs.freqtrade.io/en/latest/backtesting/
- Freqtrade lookahead analysis : https://www.freqtrade.io/en/latest/lookahead-analysis/
- OWASP ASVS 5.0.0 : https://asvs.owasp.org/
- OpenAPI Specification : https://spec.openapis.org/oas/latest.html
- AsyncAPI : https://www.asyncapi.com/docs/reference/specification/latest
- PostgreSQL : https://www.postgresql.org/docs/

## Interprétation scientifique

Références de travail recommandées : Ernest Chan, *Algorithmic Trading* (hypothèses quant), Larry Harris, *Trading and Exchanges* (microstructure), Marcos López de Prado, *Advances in Financial Machine Learning* (risques de validation), William Feller (probabilités) et littérature sur Automated Market Makers. Ces ouvrages sont suggérés, non cités comme preuve de rentabilité. Vérifier licences et conventions de données à chaque implémentation.

## Ce que nous ne savons pas

Le logiciel original ne dévoile ni son code ni la définition exacte de ses indicateurs ou agents. Ses gains affichés sont invérifiables. Les captures ne prouvent ni l'origine de la blockchain, ni l'usage d'un LLM, ni un vrai compte. Les écrans d'administration présentés sont des vues proposées **créées pour le projet**.
