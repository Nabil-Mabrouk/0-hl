---
id: fees-spread-slippage
kind: basics
title: Fees, spread and slippage
strap: Why a trade must gain almost one percent just to break even — and why that rules out most ideas.
order: 3
reading_minutes: 4
---

## The three costs of a trade

Every time a bot buys and later sells, it pays three costs:

- **The exchange fee.** Our bots take the price that is offered right now (they are *takers*), which costs **0.40 %** of the amount on each side: once to buy, once to sell.
- **The spread.** At any moment, the best price to buy is a little higher than the best price to sell. Buying and selling straight away loses that gap. The bots refuse to trade when the gap is too wide.
- **Slippage.** A large order may need more than the best offer and eat into the next, slightly worse prices of the order book. Our simulation walks through the real order book to measure it; our studies assume about **0.10 %**.

Added up, a **round trip costs about 0.9 %**.

## What that means

A trade must therefore gain about 0.9 % before it earns a single cent. Consider a strategy that is "right" more often than not:

| | Before costs | After costs |
|---|---|---|
| Average winning trade | +1.0 % | +0.1 % |
| Average losing trade | −1.0 % | −1.9 % |

Even winning 60 % of the time, this strategy loses money after costs. This is exactly what happened to our first strategy, *Volume Spike*: its typical move was a few tenths of a percent, far below the cost of a round trip.

## How the strategies respond

- **Aim for larger moves.** Hold for hours or days rather than minutes, so that a typical move is several times the cost.
- **Trade less.** Every trade pays the full cost; fewer, better trades lose less to fees.
- **Never trade a wide spread.** Each bot has a maximum spread, checked before every order.
- **Count everything.** Every result shown on 0-hl — on the radar, in the Terminal, in the strategy sheets — is **after** fees and slippage.

A cheaper way exists: placing orders that wait in the book (*maker* orders) pays lower fees, but they are not always filled. Our bots do not use them yet.
