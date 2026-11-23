---
id: reading-the-radar
kind: basics
title: Reading the market radar
strap: One picture of the whole market against BTC — and of what each bot just decided.
order: 2
reading_minutes: 3
---

## The two axes

The [market radar](/) puts **BTC at the centre** and places every other coin relative to it, over the last hour:

- **Left to right: performance against BTC.** A coin on the right went up more (or fell less) than BTC over the hour; on the left, it did worse. The distance is in *percentage points*: if BTC is up 1 % and SOL up 2.4 %, SOL sits at +1.4.
- **Bottom to top: volatility against BTC.** A coin higher up moved more nervously, minute by minute, than BTC; lower down, it was calmer. The scale doubles at each step away from the centre, so "twice as volatile" and "half as volatile" are at the same distance.

A coin near the centre behaved like BTC. Hover a point (or use *Inspect pair*) to see its exact values.

## Grey and pinned points

- A **grey, hollow point** traded too rarely during the hour for its volatility to mean anything. It is shown on the horizontal axis only.
- A point **pinned to the edge with a small arrow** is beyond the scale; its real value is in the readout.

## The bots

The cards on the right are the paper bots. Each one shows its strategy, its status, its result since it started and five steps: **Scan, Check, Size, Execute, Equity** (see *What 0-hl is*).

Every minute, each bot evaluates every coin. The radar does not draw forty wires per bot: it shows a counter ("40 scanned · 1 signal") and draws a **wire** only for what matters:

- a coin that gave a **signal** — the wire continues into the bot's steps, which light up as far as the decision went, and stop in red at the step that refused it, with the reason;
- a coin the bot **holds** — marked by a coloured ring around the point;
- the coin you **selected**.

## What it is good for

At a glance you can see whether the market is moving together or splitting apart, which coins are leading or lagging BTC, and why each bot is — most of the time — doing nothing. Each [strategy sheet](/guide) shows the part of the radar where that strategy tends to buy.

The radar replays the last minute of decisions; it is not a forecast.
