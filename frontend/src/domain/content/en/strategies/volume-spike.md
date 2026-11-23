---
id: volume-spike
kind: strategy
title: Volume Spike
strap: Buy a pair when a sudden burst of buying pushes its price up within five minutes — the first strategy of 0-hl, now kept as a demonstration.
order: 1
reading_minutes: 4
radar_zone: { x: [0, 3], y: [0.5, 2] }
study: { date: 2026-11-20, verdict: "No new version — entries paused", summary: "Over 2.4 days and 864 variants, no version made money after fees, and the entry filter did no better than entering at random. Almost every trade ended at the 60-minute time limit: the moves were far too small to pay a round trip of about 0.9 %. The strategy is kept running for demonstration, not because it works." }
---

## The idea in one sentence

When a coin suddenly trades several times its usual volume, mostly through buyers, and its price is already moving up, a short burst of momentum may follow.

{{live}}

## Why it could work

News, a listing, a large order or a social-media wave often show up first as an unusual burst of trading. If most of that trading comes from buyers who accept the asking price, demand is outrunning supply for a moment, and the price may keep climbing for a few minutes after the burst is spotted.

## The rules

These are the exact rules of the version the paper bot is running right now. They are read from the live configuration, not copied by hand.

{{rules}}

## Where it buys on the radar

{{radar}}

Volume Spike reacts to pairs that suddenly get busy and move up: on the market radar they usually sit a little to the right of BTC and higher up, because a burst of trading also makes the price more volatile.

## Latest trades

{{trades}}

## What we learned

Volume Spike was the first strategy of 0-hl, and the paper bot showed its weakness quickly:

- **The moves were too small.** Most trades ended at the one-hour time limit, neither reaching the target nor the stop. A typical gain of a few tenths of a percent cannot pay for about **0.9 %** of fees and slippage per round trip.
- **Too many trades.** A five-minute signal fires often, and every trade pays the full cost.
- **The filter did not help.** In the study, entering at random times did about as well as entering on a volume spike.

This is why later strategies aim for moves of several hours or days, with far fewer trades.

## What the study says

{{study}}

## Risks and limits

- **Fees decide everything.** Short, frequent trades are the hardest way to beat a round-trip cost of almost one percent.
- **Bursts fade.** A spike often ends as quickly as it started; buying after it has been detected means buying late.
- **Paper, not live.** Prices and order books are real (Kraken), the execution is simulated and the capital is fictitious. Nothing here is investment advice.

## Version history

{{versions}}
