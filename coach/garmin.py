"""Garmin Connect client: login with a persistent token + data reads.

Reuses the saved token (avoids re-login and the login rate limit); only uses the
email/password from .env when there is no valid token.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TOKENSTORE = PROJECT_ROOT / ".garmintokens"


class GarminAuthError(RuntimeError):
    """Authentication failure (missing/example credentials, or login rejected)."""


def connect() -> Any:
    """Connect to Garmin reusing the token; log in with a password only if needed."""
    load_dotenv()
    from garminconnect import Garmin

    if TOKENSTORE.exists():
        try:
            client = Garmin()
            client.login(str(TOKENSTORE))
            return client
        except Exception:
            pass  # expired/invalid token — fall back to password login

    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    if not email or not password:
        raise GarminAuthError("GARMIN_EMAIL / GARMIN_PASSWORD not set in .env")
    if "example.com" in email:
        raise GarminAuthError(".env still has the example values")

    client = Garmin(email=email, password=password)
    client.login()
    TOKENSTORE.mkdir(exist_ok=True)
    client.client.dump(str(TOKENSTORE))
    return client


def fetch_activities(client: Any, days: int = 120) -> list[dict[str, Any]]:
    """Activity summaries from the last `days` days — enough window for CTL/ATL."""
    end = date.today()
    start = end - timedelta(days=days)
    return client.get_activities_by_date(start.isoformat(), end.isoformat()) or []


def fetch_wellness(
    client: Any, days: int = 60
) -> list[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Daily wellness for the last `days` local dates.

    Returns (date_iso, user_summary, sleep_data) tuples. Robust to missing data
    (e.g. sleeping without the watch → empty sleep_data). The user summary carries
    resting HR, stress, and Body Battery high/low in a single call.
    """
    end = date.today()
    out: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    for i in range(days):
        day = (end - timedelta(days=i)).isoformat()
        try:
            summary = client.get_user_summary(day) or {}
        except Exception:
            summary = {}
        try:
            sleep = client.get_sleep_data(day) or {}
        except Exception:
            sleep = {}
        out.append((day, summary, sleep))
    return out


def fetch_race_predictions(client: Any) -> dict[str, Any] | None:
    """Garmin's projected race times (5k/10k/half/marathon) — current snapshot."""
    try:
        return client.get_race_predictions() or None
    except Exception:
        return None


def fetch_current_vo2max(client: Any) -> tuple[float | None, int | None]:
    """Current VO2max + fitness age (a slowly-changing profile metric)."""
    try:
        ts = client.get_training_status(date.today().isoformat()) or {}
    except Exception:
        return None, None
    vo2 = ts.get("mostRecentVO2Max")
    generic = vo2.get("generic") if isinstance(vo2, dict) else None
    if not isinstance(generic, dict):
        return None, None
    return generic.get("vo2MaxValue"), generic.get("fitnessAge")
