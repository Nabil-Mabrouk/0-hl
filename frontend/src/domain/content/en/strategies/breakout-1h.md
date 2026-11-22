---
id: breakout-1h
kind: strategy
title: Breakout 1h
strap: Buy a pair when it breaks above its last-hour high on strong volume, while its longer trend is up.
order: 2
reading_minutes: 5
radar_zone: { x: [0, 3], y: [0, 1.5] }
study: { date: 2026-11-21, verdict: "No launch", criteria: "0 of 5", summary: "Over 3 days of a falling market, breakouts did worse than random entries: most were false breakouts stopped out within hours. 23 trades in total — far too few to judge the idea itself, only this market regime. The study is re-run every week." }
---

## The idea in one sentence

When a price pushes through the highest level of the past hour, with more trading than usual and inside a rising trend, buyers are often still arriving — and the move can carry on for a few hours.

{{live}}

## Why it could work

A one-hour high is a level many traders watch. When the price closes above it:

- sellers who were waiting at that level have been absorbed;
- traders who placed buy orders above the high get filled and add to the push;
- if the market was already rising over the last four hours, the breakout goes *with* the trend rather than against it.

The volume condition matters: a breakout on thin trading is often just noise, while a breakout on two or three times the usual volume shows that real money is behind it.

## The rules

These are the exact rules of the version the paper bot is running right now. They are read from the live configuration, not copied by hand.

{{rules}}

Two details are deliberate:

- **It does not chase.** If the price is already far above the one-hour high, the bot stays out: the easy part of the move is gone and the risk of a pull-back is high.
- **It needs a real range.** The range of the past hour must be wide enough for a breakout to mean something, but not so wide that the market is simply chaotic.

## Where it buys on the radar

{{radar}}

On the market radar, Breakout 1h looks at pairs that are starting to beat BTC and are a little more volatile than BTC: the upper-right part of the chart, close to the centre. Pairs far out on the right have usually already run, and the "no chasing" rule keeps the bot away from them.

## Latest trades

{{trades}}

## When it works — and when it doesn't

It tends to work when the whole market is calm or rising, money rotates from coin to coin, and a pair that breaks out keeps attracting buyers for a few hours.

It struggles when the market is falling: breakouts become *false* breakouts — the price pokes above the high, then falls back and hits the stop. It also struggles when moves are too small: a round trip costs about **0.9 %** in fees and slippage, so a breakout that only adds half a percent loses money even when it is "right".

## What the study says

Before a strategy is judged, it is replayed on recorded market data with rules fixed in advance (see *How we judge a strategy*). This is a summary; the bot below keeps running in paper so that the next studies have more data.

{{study}}

## Risks and limits

- **Fees decide everything.** The strategy only makes sense if the average winning move is well above the cost of a round trip.
- **Few trades.** A pair can only be traded again 4 hours after an exit, so results take weeks to become meaningful. A handful of trades proves nothing, good or bad.
- **Paper, not live.** Prices and order books are real (Kraken), the execution is simulated and the capital is fictitious. Nothing here is investment advice.

## Version history

{{versions}}
