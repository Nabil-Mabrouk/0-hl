---
id: what-is-0-hl
kind: basics
title: What 0-hl is
strap: Trading bots that run on real Kraken prices with fictitious money, and show every decision they make.
order: 1
reading_minutes: 4
---

## In one paragraph

0-hl runs trading bots on the live market of [Kraken](https://www.kraken.com), one of the large crypto exchanges. The prices, the trades and the order books are real, read every second. The money is not: each bot starts with a fictitious capital and its orders are **simulated** against the real order book, with real fees. This is called **paper trading**. It lets us test trading ideas in real conditions without risking anything — and show the results openly, good or bad.

## What a bot does, step by step

Every minute, each bot looks at the most traded coins quoted in US dollars (up to 40) and goes through the same five steps:

1. **Scan** — it computes a few measures for each coin (volume, price changes, trend…) and checks them against the rules of its strategy. Most of the time, nothing qualifies.
2. **Check** — when a coin qualifies, a safety gate looks at the market itself: is the gap between buy and sell prices small enough? Is there enough volume in the order book? Is the data fresh? If not, the trade is refused.
3. **Size** — the bot decides how much to buy, so that hitting the stop-loss would only lose a small, fixed share of its capital. It also respects limits: number of open positions, daily loss, total drawdown.
4. **Execute** — the order is filled against the real order book, as a real order would be: walking through the available sell offers, paying the exchange fee.
5. **Equity** — every open position is valued continuously, and every closed trade updates the bot's balance.

You can watch these steps live on the [market radar](/) of the home page and in the Terminal.

## What makes it different

- **Every decision has a reason.** Each scan, each refusal and each trade is recorded with the numbers that led to it.
- **Every fee is counted.** Results are always shown after fees, because fees are what kills most ideas (see *Fees, spread and slippage*).
- **Strategies are tested before they are judged.** Each one is replayed on recorded market data with rules fixed in advance (see *How we judge a strategy*).
- **Results are public.** The [strategy sheets](/guide) show each bot's real paper result, in percent.

## What it is not

- **Not live trading.** No real order is ever sent. Trading real money would require an explicit decision that has not been taken.
- **Not a product to copy.** None of the strategies has proven an edge yet; several have clearly failed, and we say so.
- **Not investment advice.** 0-hl is an engineering and research project.
