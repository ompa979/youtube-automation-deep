"""Tracks daily YouTube quota usage per rotating project.

YouTube Data API v3 free tier = 10,000 units/day.
Each video upload = 1,600 units → 6 uploads per project per day.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from .config import ROOT

STATE_PATH = ROOT / ".quota_state.json"
UPLOAD_COST = 1600
DAILY_QUOTA = 10000

@dataclass
class QuotaState:
    day: str
    used: dict[str, int] = field(default_factory=dict)

def _now_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def load_state() -> QuotaState:
    if not STATE_PATH.exists():
        return QuotaState(day=_now_day(), used={})
    try:
        data = json.loads(STATE_PATH.read_text())
    except Exception:
        return QuotaState(day=_now_day(), used={})

    if data.get("day") != _now_day():
        return QuotaState(day=_now_day(), used={})

    return QuotaState(day=data["day"], used=data.get("used", {}))

def save_state(state: QuotaState) -> None:
    STATE_PATH.write_text(json.dumps(
        {"day": state.day, "used": state.used},
        indent=2,
    ))

def pick_project(project_names: list[str]) -> tuple[str | None, QuotaState]:
    state = load_state()
    for name in project_names:
        used = state.used.get(name, 0)
        if used + UPLOAD_COST <= DAILY_QUOTA:
            return name, state
    return None, state

def record_upload(project_name: str, state: QuotaState) -> None:
    state.used[project_name] = state.used.get(project_name, 0) + UPLOAD_COST
    save_state(state)
