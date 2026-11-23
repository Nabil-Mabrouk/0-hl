---
id: rebound
kind: strategy
title: Rebound after excess
strap: Buy a pair after a sharp drop of its own — while BTC holds steady — once the price starts to turn up, and sell when it is back to its recent average.
order: 3
reading_minutes: 5
radar_zone: { x: [-5, -1], y: [0.3, 2] }
study: { date: 2026-11-21, verdict: "No launch", criteria: "1 of 5", summary: "Over about 2 days, the rules as written lost money. One variant — a drop of 3 % or more, only when BTC is steady — was the most encouraging signal of all our first studies: positive before fees and better than random entries, but on 7 trades only, and still slightly negative after fees. The study is re-run every week." }
---

## The idea in one sentence

When a coin falls fast and hard on its own, while the rest of the market is calm, the selling is often overdone — and part of the drop comes back within hours.

{{live}}

## Why it could work

A sudden drop of several percent in a few hours is often driven by a few large sellers, stop-losses being triggered one after another, or a rushed reaction to news. Once that selling is over, buyers who think the price is now too low step in, and the price drifts back towards where it was trading before.

Two conditions separate an *excess* from a real change of value:

- **It must be the pair's own drop.** If BTC is falling at the same time, the whole market is under pressure and there is no reason for a quick recovery.
- **The pair must not be collapsing.** A coin already down sharply over the day may be falling for a good reason.

This strategy is the mirror image of *Breakout 1h*: one bets that a move continues, the other that an excess fades.

## The rules

These are the exact rules of the version the paper bot is running right now. They are read from the live configuration, not copied by hand.

{{rules}}

Two details are deliberate:

- **It waits for the turn.** The price must already be rising again over the last five minutes: the bot does not try to catch a falling knife.
- **It sells on a condition, not a target.** The exit comes when the price is back to its average of the last four hours — wherever that is — rather than at a fixed percentage.

## Where it buys on the radar

{{radar}}

On the market radar, Rebound after excess looks to the left — pairs that lag BTC over the last hour — and usually above the centre, because a sharp drop also makes the price more volatile.

## Latest trades

{{trades}}

## When it works — and when it doesn't

It tends to work in a calm or rising market, when a single coin is hit by a burst of selling that has nothing to do with the rest of the market.

It struggles in a broad sell-off: drops are no longer excesses, they are the market itself, and "cheap" keeps getting cheaper. It also struggles when the drop has a real cause — bad news, a delisting — that the price has not finished pricing in.

## What the study says

{{study}}

## Risks and limits

- **Catching a falling knife.** The stop-loss is wide on purpose, because rebounds are noisy; when the drop does not come back, the loss is the full stop.
- **Fees.** Even a real rebound of one or two percent is largely eaten by a round-trip cost of about **0.9 %**.
- **Few trades.** A pair can only be traded again several hours after an exit, and true excesses are rare: results take weeks to mean anything.
- **Paper, not live.** Prices and order books are real (Kraken), the execution is simulated and the capital is fictitious. Nothing here is investment advice.

## Version history

{{versions}}
