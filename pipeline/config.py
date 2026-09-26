"""Configuration and environment loading."""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
CONTENT_PLAN_PATH = ROOT / "content_plan.json"
WORK_DIR = Path("/tmp/work")
OUT_DIR = Path("/tmp/out")
ASSETS_DIR = ROOT / "assets"

@dataclass
class YouTubeCredentials:
    index: int
    payload: dict[str, Any]

    @property
    def name(self) -> str:
        return f"yt_project_{self.index}"

@dataclass
class Settings:
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None
    pexels_api_key: str | None = None
    pixabay_api_key: str | None = None
    youtube_projects: list[YouTubeCredentials] = field(default_factory=list)
    upload_enabled: bool = True
    niches_enabled: list[str] = field(default_factory=lambda: ["facts"])
    languages_enabled: list[str] = field(default_factory=lambda: ["en"])

def ensure_dirs() -> None:
    for d in (WORK_DIR, OUT_DIR, ASSETS_DIR):
        d.mkdir(parents=True, exist_ok=True)
