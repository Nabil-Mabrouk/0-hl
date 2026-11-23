---
id: rotation
kind: strategy
title: Rotation of the strongest
strap: Hold the three strongest coins of the last 24 hours as long as they keep beating BTC — few trades, positions held for days.
order: 4
reading_minutes: 5
radar_zone: { x: [0.5, 5], y: [-0.5, 1.5] }
study: { date: 2026-11-21, verdict: "Not judgeable yet", summary: "Positions last up to four days, so a fair verdict needs four weeks of recorded data — around mid-December. The first run, on about 3 days, could not complete a single trade. Until then the paper bot is the only source of results, and they are too few to mean anything." }
---

## The idea in one sentence

Coins that have done best over the last day, and better than BTC, tend to keep doing better for a while — so the strategy holds the top three and replaces them when they fall out of the lead.

{{live}}

## Why it could work

Money in crypto moves in waves: a theme, a narrative or a new listing attracts buyers, and the coins that benefit first often keep attracting attention for several days. This persistence of relative strength — *momentum* — is one of the most studied effects in markets.

Two choices make this version different from the other strategies of 0-hl:

- **It trades rarely.** It looks at a full day, holds for days and only rebalances when the ranking really changes. Fewer trades means fewer fees, which matters with a round trip at about **0.9 %**.
- **It compares with BTC.** A coin is only bought if it beats BTC; in a market where everything falls, being "the strongest" is not enough.

## The rules

These are the exact rules of the version the paper bot is running right now. They are read from the live configuration, not copied by hand.

{{rules}}

Two details are deliberate:

- **A gap between entry and exit.** A coin enters when it is in the top 3 and only leaves when it drops below the top 8, so that small changes in the ranking do not cause constant buying and selling.
- **Big positions, wide stop.** With a 10 % stop and at most three positions at once, each position is about a third of the bot's capital. This needs its own risk policy: more risk per trade than the other bots, and fewer positions.

## Where it buys on the radar

{{radar}}

Rotation of the strongest ranks coins over 24 hours, not one hour. On the radar of the home page (one hour) its picks are usually, but not always, to the right of BTC. In the Terminal, switch the radar to the 24-hour window to see the ranking it actually uses.

## Latest trades

{{trades}}

## When it works — and when it doesn't

It tends to work when a market trend lasts: the leaders of one day are still leading the next.

It struggles when the market turns often: yesterday's winner becomes today's loser, and the strategy buys high and sells lower. It also struggles in a broad decline, where no coin beats BTC for long — in that case it simply holds nothing.

## What the study says

{{study}}

## Risks and limits

- **Concentration.** Three positions of about a third of the capital each: one bad coin weighs heavily.
- **Slow verdict.** With trades lasting days, months of results are needed before any conclusion.
- **Late entries.** Buying what has already risen means sometimes buying the top.
- **Paper, not live.** Prices and order books are real (Kraken), the execution is simulated and the capital is fictitious. Nothing here is investment advice.

## Version history

{{versions}}
