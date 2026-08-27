<div align="center">

# ✈️ Travel Deal Agent

### An AI agent that watches flight & bus prices around the clock — so you don't have to.

![Status](https://img.shields.io/badge/status-live-brightgreen)
![Powered by](https://img.shields.io/badge/powered%20by-Claude-6c5ce7)
![Type](https://img.shields.io/badge/type-personal%20project-blue)

**[🚀 Try the live demo →](https://example.com/travel-demo)**

*One real, live price check per visitor per day — see it work for yourself.*
<!-- TODO: replace with the real VPS/Nginx URL once deployed -->

</div>

---

## The problem

Finding a genuinely cheap flight or bus ticket means checking prices
obsessively — multiple sites, multiple dates, multiple times a day —
and still second-guessing whether "€45" is actually a good deal or just
a normal Tuesday price. Most people give up and either overpay or miss
the window entirely.

## The idea

**What if an AI agent did that watching for you — and knew the
difference between a normal price and a real deal?**

This project is exactly that: a personal travel-deal watchdog that runs
twice a day, checks a list of routes you care about, remembers what
"normal" looks like for each one, and only interrupts you when something
is actually worth booking.

## How it works

<div align="center">

| 1️⃣ | 2️⃣ | 3️⃣ | 4️⃣ |
|:---:|:---:|:---:|:---:|
| **You describe the trips you care about** | **The agent checks prices, twice a day, automatically** | **It compares each price to your personal history** | **You get pinged only when it's genuinely a deal** |
| "Brussels to Rome, under €60" | Real fare data, not guesswork | Not a fixed number — a moving baseline that learns | Straight to your phone, with the reasoning included |

</div>

No dashboards to babysit. No tabs full of booking sites. Just a quiet
notification when it's actually worth acting on.

## What makes it more than a price alert

- 🧠 **Judges deals like a person would.** Instead of one hardcoded
  price, it learns each route's typical cost over time and flags
  anything meaningfully below that — plus a hard safety-net price for
  "always tell me about this."
- 💬 **You can just talk to it.** A built-in chat assistant lets you
  add, tweak, or remove tracked routes in plain English — no config
  files to hand-edit. It always asks for a yes before it changes
  anything.
- 🌍 **Can go looking, not just checking.** For open-ended trips
  ("somewhere cheap in Europe this weekend"), it explores options itself
  rather than needing an exact destination up front.
- ✍️ **Writes its own alerts.** The notification you get isn't a raw
  price dump — an AI model writes a short, human summary of why the
  deal is worth your attention.
- 🩺 **Watches its own health.** If a check silently breaks — a bad
  run, a dead schedule — that's visible on a monitoring dashboard, not
  discovered three weeks later as "huh, I haven't gotten any alerts."

## Why this project exists

Two reasons: it's something I actually use to catch cheap weekend trips
out of Brussels, and it's a hands-on demonstration of building an AI
agent that makes real decisions with real money on the line — safely,
with a human always in the loop for anything that changes its
configuration, not just a chatbot wrapper.

<div align="center">

**[🚀 Try the live demo →](https://example.com/travel-demo)**

</div>

---

<div align="center">

🛠️ Curious how it's actually built? **[Read the technical write-up →](docs/ARCHITECTURE.md)**

</div>
