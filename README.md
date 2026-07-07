# Running Performance

Personal running dashboard for a single athlete, integrated with Garmin Connect. It turns
your Garmin data into training-load metrics, planned-vs-actual tracking, and recovery/risk
signals — all local and private.

## How it works

- **Deterministic tracking.** The app syncs Garmin data, computes fitness/fatigue/form
  (CTL/ATL/TSB), tracks your plan against what you actually did, and flags overload. It
  only measures and stores — it makes no training decisions.
- **Verified plans.** You set and adjust your training plan; an imported plan must pass a
  deterministic safety verifier before it is tracked, so an unsafe plan is never saved.

## Dashboard

Five screens (Portuguese UI, white + purple, interactive, fully offline):
**Visão geral** · **Condição & carga** · **Treinos** · **Recuperação** · **Relatórios**.

## Setup

```bash
python -m venv venv
venv\Scripts\activate            # Windows
# source venv/bin/activate       # Linux / macOS
pip install -r requirements.txt
```

Create a `.env` (never committed) with your Garmin login:

```
GARMIN_EMAIL=you@example.com
GARMIN_PASSWORD=your-password
```

## Commands

```bash
python -m coach.migrate    # apply the database schema
python -m coach.sync       # pull activities + wellness from Garmin
python -m coach.load       # recompute training load (CTL/ATL/TSB)
python -m coach.daily      # full update: sync -> load -> match -> alerts
python -m coach.web        # local dashboard at http://127.0.0.1:8000
```

Schedule `coach.daily` (Windows Task Scheduler or `cron`) to keep the data fresh
automatically.

## Privacy

Everything stays local. The database (`*.db`), `.env`, and Garmin tokens are gitignored
and never leave your machine.

## Stack

Python 3.11+ · SQLite · [`garminconnect`](https://github.com/cyberjunky/python-garminconnect)
· FastAPI + uvicorn. Server-rendered HTML with inline SVG charts — no SPA, no CDN.

Personal, single-user project.
