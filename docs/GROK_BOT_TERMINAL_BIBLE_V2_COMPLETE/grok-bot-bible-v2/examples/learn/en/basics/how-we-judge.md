---
id: how-we-judge
kind: basics
title: How we judge a strategy
strap: Rules fixed in advance, a control period, a random baseline — and why a few good trades prove nothing.
order: 5
reading_minutes: 5
---

## The trap: finding patterns in noise

Markets are noisy. Try enough variations of a strategy on past data and one of them will look great — by pure chance. This is called **overfitting**: the strategy has learned the past, not a lasting effect, and fails as soon as it meets new data. Most of our method exists to avoid fooling ourselves.

## Step 1 — a study on recorded data

0-hl records every minute of trading and the order books of the coins it watches. A **study** replays a strategy on that history, minute by minute, using the same code that the bots use, with the same fees and an allowance for slippage, and without ever looking at data from the future.

A study tries the strategy as written plus a grid of variations (different thresholds, stops, holding times) — often a few hundred. The report always says how many were tried, because the more variations, the more likely a lucky one.

## Step 2 — a control period

The history is split in two:

- the **first part** (the first 60 % of the period) is used to pick the best variation;
- the **second part** (the last 40 %) is used to **judge** it. The chosen variation has never "seen" this data.

A variation that only works on the first part was a lucky find.

## Step 3 — a random baseline

The same exits are applied to **random entries** — one per coin and per hour, with no filter. This shows what the market itself gave over the period. A strategy must beat this baseline, not just make money: in a rising market almost anything makes money; in a falling one almost anything loses.

## Step 4 — criteria fixed in advance

Before seeing any result, each study fixes its criteria. For most of our strategies, on the control period:

1. at least **20 trades**;
2. an average result **after fees** above zero;
3. total gains at least **1.2 times** total losses, after fees;
4. an average result before fees at least **0.3 points** better than the random baseline;
5. a positive result after fees on the first part too.

All five must hold. If not, nothing is concluded and the study is **re-run every week** as the history grows.

## Where the paper bots fit

A paper bot can run a strategy before its study passes — the money is fictitious, and real conditions teach things a study cannot (real spreads, real fills, the engine running non-stop). But a paper bot's results are **observation, not proof**: a few weeks and a few dozen trades are too few to separate skill from luck.

That is why every [strategy sheet](/guide) shows both: the live paper result, and the latest verdict of the study, with its date.
