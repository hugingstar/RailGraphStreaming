# -*- coding: utf-8 -*-
"""Runtime configuration and the shared simulation clock."""
from __future__ import annotations

import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Repo root .env (not backend/.env) -- shared by every service, python or not.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")

KST = timezone(timedelta(hours=9))

KAFKA_BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:19092")

TOPIC_PLANS = "rail.plans"
TOPIC_OBSERVATIONS = "rail.observations"
TOPIC_POSITIONS = "rail.positions"
TOPIC_ALERTS = "rail.alerts"
ALL_TOPICS = (TOPIC_PLANS, TOPIC_OBSERVATIONS, TOPIC_POSITIONS, TOPIC_ALERTS)

# How often the estimator republishes every active train.
TICK_S = float(os.getenv("RAILGRAPH_TICK_S", "1.0"))
# How far ahead of departure a plan is published.
PLAN_HORIZON_S = float(os.getenv("RAILGRAPH_PLAN_HORIZON_S", "1800"))
# How long a train stays on the map after arriving.
RETIRE_AFTER_S = float(os.getenv("RAILGRAPH_RETIRE_AFTER_S", "180"))
# Report from roughly one in N commercial stops; the rest is inference.
OBS_EVERY_N_STOPS = int(os.getenv("RAILGRAPH_OBS_EVERY_N_STOPS", "3"))
# Delay (seconds) at which a train is flagged.
ALERT_DELAY_S = float(os.getenv("RAILGRAPH_ALERT_DELAY_S", "300"))
ALERT_CLEAR_S = float(os.getenv("RAILGRAPH_ALERT_CLEAR_S", "180"))

API_HOST = os.getenv("RAILGRAPH_API_HOST", "0.0.0.0")
API_PORT = int(os.getenv("RAILGRAPH_API_PORT", "8123"))

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://railgraph:railgraph@localhost:15432/railgraph"
)
SESSION_COOKIE = "rg_session"
SESSION_TTL_S = int(os.getenv("RAILGRAPH_SESSION_TTL_S", str(60 * 60 * 24 * 14)))  # 14 days

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-flash-lite-latest")
GEMINI_EMBEDDING_MODEL = "models/gemini-embedding-001"
EMBEDDING_DIM = 768

# Wall-clock multiplier.  1.0 = real time, which is what a live map wants;
# raise it to watch a whole operating day compress into a demo.
SPEED = float(os.getenv("RAILGRAPH_SPEED", "1.0"))


def _anchor() -> float:
    """Midnight KST of the current wall-clock day.

    Every service derives simulated time from this same constant, so separate
    processes agree on 'now' without having to exchange a clock.
    """
    today = datetime.now(KST).date()
    return datetime.combine(today, time(0, 0), tzinfo=KST).timestamp()


def now() -> float:
    """Current simulated epoch seconds."""
    wall = datetime.now(timezone.utc).timestamp()
    if SPEED == 1.0:
        return wall
    a = _anchor()
    return a + (wall - a) * SPEED


def kst(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, KST)


def hhmm(ts: float) -> str:
    return kst(ts).strftime("%H:%M")
