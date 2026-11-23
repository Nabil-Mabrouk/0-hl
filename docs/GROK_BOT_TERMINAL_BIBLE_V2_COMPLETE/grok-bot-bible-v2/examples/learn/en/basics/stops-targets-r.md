---
id: stops-targets-r
kind: basics
title: Stops, targets and R
strap: How a bot decides how much to buy, and how it measures a trade in units of risk.
order: 4
reading_minutes: 4
---

## The stop-loss comes first

Before buying, a bot already knows where it will give up: the **stop-loss**, a price below the entry at which it sells to cut the loss. Each strategy sets it as a percentage — for example 2.5 % below the entry price.

## The size follows from the risk

The bot does not buy a fixed amount. It decides how much of its capital it accepts to lose if the stop is hit — for most bots **0.5 %** — and works out the size from there:

> capital 10,000 · risk 0.5 % = 50 at risk
> stop 2.5 % below the entry ⇒ position of 50 / 2.5 % = **2,000**

With a wider stop, the position is smaller; with a tighter stop, it is larger. Either way, hitting the stop costs about the same share of the capital (plus fees). Other limits then apply: a maximum share of the capital per coin, a maximum number of open positions, and a stop to new trades after a bad day or a deep drawdown.

## R: the unit of risk

The amount at risk on a trade is called **1R**. Results can then be compared across trades of different sizes:

- a trade that hits its stop loses about **1R**;
- a trade that gains twice the amount at risk makes **+2R**;
- a **target of 2R** means selling when the gain reaches twice the distance to the stop — with a 2.5 % stop, at +5 %.

Some strategies do not use a fixed target: they sell when a condition is met (for example, the price is back to its recent average) or when a time limit is reached.

## Why it matters

Thinking in R separates two questions: *how often* a strategy wins, and *how much* it wins compared with what it risks. A strategy that wins only 40 % of the time can still make money if its winners average +2R and its losers −1R — before fees. After fees (see *Fees, spread and slippage*), every R must be large enough to absorb about 0.9 % per round trip.
