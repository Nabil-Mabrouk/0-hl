---
id: pullback
kind: strategy
title: Pullback in an uptrend
strap: In a rising market, buy a rising coin after a short dip, once it starts to climb again, and sell when it is back near its recent high.
order: 5
reading_minutes: 5
radar_zone: { x: [-3, 0], y: [0, 1.5] }
study: { verdict: "Not studied yet", summary: "This strategy was built from the lessons of the first studies (November 2026). Its own study runs on the recorded data; in a falling market its conditions are rarely met, so it needs a rising period to produce enough trades to judge." }
---

## The idea in one sentence

When the whole market is going up, a short dip in a coin that is itself rising is more often a pause than the start of a fall — buying the dip and selling near the recent high can capture the rebound.

{{live}}

## Why it could work

Our first studies, run in a falling market, taught us two things: a strategy that only buys cannot make money when everything goes down, and the only encouraging signal was buying a drop that was specific to one coin. *Pullback in an uptrend* combines both lessons:

- it only trades when **BTC is up** over the day and **more than half of the coins** are up too;
- it only buys a coin that is **itself up** over the day;
- inside that rising context, it buys a **short dip** from the coin's recent high, once the price starts to rise again.

In a rising market, dips are often bought quickly by traders who missed the first move. The goal is not to catch a big trend, only the return to the recent high.

## The rules

These are the exact rules of the version the paper bot is running right now. They are read from the live configuration, not copied by hand.

{{rules}}

Two details are deliberate:

- **It sits out falling markets.** When BTC or most coins are down, it does not trade at all. Long periods without trades are expected, not a bug.
- **A modest target.** It sells as soon as the price is back close to its high of the last three hours, instead of hoping for more.

## Where it buys on the radar

{{radar}}

On the market radar, Pullback in an uptrend looks at coins that have just dipped, so they usually sit slightly to the left of BTC over the last hour — while the coin is still up over the whole day.

## Latest trades

{{trades}}

## When it works — and when it doesn't

It tends to work in steady uptrends, when dips are short and buyers come back quickly.

It struggles at the turning point of a market: a dip that looks like a pause is sometimes the first step of a reversal, and the stop-loss is hit. It does nothing in a falling market — which protects the capital, but also means no result to judge for long stretches.

## What the study says

{{study}}

## Risks and limits

- **Long silences.** The strategy may not trade for days or weeks; results accumulate slowly.
- **Reversals.** The first dip of a market top looks exactly like a healthy pullback.
- **Fees.** The return to the recent high must be clearly larger than a round-trip cost of about **0.9 %**.
- **Paper, not live.** Prices and order books are real (Kraken), the execution is simulated and the capital is fictitious. Nothing here is investment advice.

## Version history

{{versions}}
