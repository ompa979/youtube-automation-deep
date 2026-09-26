"""YouTube Shorts automation pipeline."""
__version__ = "3.0.0"

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/config.py">

```
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
WORK_DIR = ROOT / "work"
OUT_DIR = ROOT / "out"
ASSETS_DIR = ROOT / "assets"
MANIM_MEDIA_DIR = WORK_DIR / "manim"

@dataclass
class YouTubeCredentials:
    index: int
    payload: dict[str, Any]

    @property
    def name(self) -> str:
        return f"yt_project_{self.index}"

@dataclass
class Settings:
    # Script generation
    gemini_api_key: str | None = None
    openrouter_api_key: str | None = None

    # Image generation
    gemini_image_model: str = "gemini-3.1-flash-image"

    # Visuals
    pexels_api_key: str | None = None

    # TTS (Chatterbox sidecar)
    tts_server_url: str = ""
    tts_server_token: str = ""
    tts_timeout: int = 180

    # Manim
    manim_enabled: bool = False

    # YouTube
    youtube_projects: list[YouTubeCredentials] = field(default_factory=list)
    upload_enabled: bool = True

    # Rotation
    niches_enabled: list[str] = field(default_factory=lambda: ["general_science"])
    languages_enabled: list[str] = field(default_factory=lambda: ["en"])

def _decode_creds(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        try:
            return json.loads(raw)
        except Exception:
            return None

def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}

def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]

def load_settings() -> Settings:
    youtube_projects: list[YouTubeCredentials] = []
    for i in range(1, 21):
        raw = os.getenv(f"YT_CREDS_{i}")
        if not raw:
            continue
        payload = _decode_creds(raw)
        if payload:
            youtube_projects.append(YouTubeCredentials(index=i, payload=payload))

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY") or None,
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        gemini_image_model=os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image"),
        pexels_api_key=os.getenv("PEXELS_API_KEY") or None,
        tts_server_url=(os.getenv("TTS_SERVER_URL") or "").rstrip("/"),
        tts_server_token=os.getenv("TTS_SERVER_TOKEN", ""),
        tts_timeout=int(os.getenv("TTS_TIMEOUT", "180")),
        manim_enabled=_truthy(os.getenv("MANIM_ENABLED"), False),
        youtube_projects=youtube_projects,
        upload_enabled=_truthy(os.getenv("UPLOAD_ENABLED"), True),
        niches_enabled=_csv_env("NICHES_ENABLED", ["general_science"]),
        languages_enabled=_csv_env("LANGUAGES_ENABLED", ["en"]),
    )

def ensure_dirs() -> None:
    for d in (WORK_DIR, OUT_DIR, ASSETS_DIR, MANIM_MEDIA_DIR):
        d.mkdir(parents=True, exist_ok=True)
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/script_gen.py">

```
"""Script generation — OpenRouter PRIMARY, Gemini fallback.

OpenRouter models to try in order (configurable via OPENROUTER_MODELS):
  1. deepseek/deepseek-chat-v3.1:free
  2. meta-llama/llama-3.3-70b-instruct:free
  3. google/gemini-2.0-flash-exp:free

Gemini 2.5 Flash is used ONLY if all OpenRouter models fail.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict, field

import requests

@dataclass
class Scene:
    index: int
    narration: str
    on_screen_text: str
    visual: dict = field(default_factory=dict)

@dataclass
class Script:
    title: str
    hook: str
    description: str
    tags: list[str]
    scenes: list[Scene]

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "hook": self.hook,
            "description": self.description,
            "tags": self.tags,
            "scenes": [asdict(s) for s in self.scenes],
        }

DEFAULT_OPENROUTER_MODELS = [
    "deepseek/deepseek-chat-v3.1:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemini-2.0-flash-exp:free",
]

def _openrouter_models() -> list[str]:
    raw = os.getenv("OPENROUTER_MODELS", "").strip()
    if raw:
        return [m.strip() for m in raw.split(",") if m.strip()]
    single = os.getenv("OPENROUTER_MODEL", "").strip()
    if single:
        return [single]
    return DEFAULT_OPENROUTER_MODELS

_MANIM_INSTRUCTIONS = """
When type is "concept_animation", fill "manim_code" with a COMPLETE, self-contained
Manim scene that visually explains this specific beat. Rules:
  - Start with: from manim import *
  - Define EXACTLY ONE class: class MainScene(Scene):
  - Do NOT set config.pixel_width, config.pixel_height, config.frame_width,
    config.frame_height, or config.background_color (the pipeline sets these).
  - Do NOT import os, sys, subprocess, shutil, or use open(), eval(), exec().
  - Do NOT make network calls or read files.
  - Total animation must run roughly <DURATION> seconds.
  - Use only Manim primitives: Text, MathTex, Circle, Square, Rectangle,
    Arrow, Line, Dot, VGroup, Create, Write, FadeIn, FadeOut, Transform,
    Indicate, ApplyWave, SurroundingRectangle, Brace, Axes, NumberPlane.
  - Use WHITE background-friendly colors (BLACK, DARK_GRAY, and the accent
    color #4d6bfe rendered as ManimColor("#4d6bfe")).
  - If text is Hinglish, keep labels short (<6 words).
  - If no animation makes sense for this beat, still produce a short scene
    (5-8 seconds) that visualizes the key concept with shapes and labels.
"""

def _build_prompt(topic: str, niche_cfg: dict, language: str, target_seconds: int) -> str:
    lang_instruction = {
        "en": "Write in clear, conversational English.",
        "hi": "Write in natural Hindi (Devanagari script). Keep it simple and spoken, not literary.",
        "hinglish": (
            "Write in casual Hinglish — a natural mix of Hindi and English as spoken by urban "
            "Indian youth. Use Roman script for the Hindi parts (e.g. 'yaar', 'matlab', 'bilkul'). "
            "Technical terms stay in English."
        ),
    }.get(language, "Write in English.")

    per_scene_duration = max(int(target_seconds / 5), 5)

    return f"""
{niche_cfg['system_prompt']}

{lang_instruction}

TOPIC: {topic}

Produce a YouTube Short script. Output ONLY a JSON object with this exact shape
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/tts.py">

```
"""TTS client — talks to the Chatterbox sidecar, with Edge-TTS fallback.

Provider cascade:
  1. Chatterbox sidecar at TTS_SERVER_URL (localhost:8000 in GitHub Actions)
  2. Edge-TTS (hi-IN voices) — always available, robotic but reliable
"""
from __future__ import annotations

import asyncio
import re
import shutil
import subprocess
from pathlib import Path

import requests

from .config import WORK_DIR

# Voice selection per language. The Chatterbox server exposes voices by name.
CHATTERBOX_VOICE_MAP = {
    "en":       "hindi_female",
    "hi":       "hindi_female",
    "hinglish": "hindi_female",
}

EDGE_VOICE_MAP = {
    "en":       "en-IN-PrabhatNeural",
    "hi":       "hi-IN-MadhurNeural",
    "hinglish": "en-IN-PrabhatNeural",
}

# ─── Chatterbox HTTP client ──────────────────────────────────────────────────

def _synth_chatterbox(text: str, voice: str, audio_path: Path, ass_path: Path,
                      server_url: str, token: str, timeout: int) -> float:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Auth-Token"] = token

    payload = {
        "text": text,
        "voice": CHATTERBOX_VOICE_MAP.get(voice, "hindi_female"),
        "language": voice,
        "temperature": 0.5,
    }

    r = requests.post(f"{server_url}/synthesize", headers=headers, json=payload, timeout=timeout)
    if not r.ok:
        raise RuntimeError(f"Chatterbox HTTP {r.status_code}: {r.text[:300]}")

    wav_path = audio_path.with_suffix(".wav")
    wav_path.write_bytes(r.content)

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found")

    subprocess.run(
        [ffmpeg, "-y", "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "4", str(audio_path)],
        check=True, capture_output=True, timeout=60,
    )
    wav_path.unlink(missing_ok=True)

    duration = _audio_duration(audio_path) or max(1.0, len(text.split()) / 2.4)
    _write_estimated_ass(text, duration, ass_path)
    return duration

# ─── Edge-TTS fallback ───────────────────────────────────────────────────────

async def _synth_edge_async(text: str, voice: str, audio_path: Path, ass_path: Path) -> float:
    import edge_tts

    edge_voice = EDGE_VOICE_MAP.get(voice, "en-IN-PrabhatNeural")
    communicate = edge_tts.Communicate(text, edge_voice)

    words: list[tuple[float, float, str]] = []
    last_end = 0.0

    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 10_000_000
                dur = chunk["duration"] / 10_000_000
                words.append((start, start + dur, chunk["text"]))
                last_end = start + dur

    if not audio_path.exists() or audio_path.stat().st_size == 0:
        raise RuntimeError("Edge TTS returned no audio")

    duration = _audio_duration(audio_path) or last_end
    _write_ass(words, ass_path)
    return duration

def _synth_edge(text: str, voice: str, audio_path: Path, ass_path: Path) -> float:
    return asyncio.run(_synth_edge_async(text, voice, audio_path, ass_path))

# ─── Shared helpers ──────────────────────────────────────────────────────────

def _audio_duration(path: Path) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, check=True, timeout=20,
        )
        value = float(result.stdout.strip())
        return value if value > 0 else None
    except Exception:
        return None

def _ass_timestamp(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"

def _write_ass(words: list[tuple[float, float, str]], out_path: Path) -> None:
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        "PlayResX: 1080\n"
        "PlayResY: 1920\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour,"
        " Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow,"
        " Alignment, MarginL, MarginR, MarginV, Encoding\n"
        "Style: Cap,Inter,86,&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,"
        "-1,0,0,0,100,100,0,0,1,4,2,2,60,60,220,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    lines: list[str] = []
    window = 3
    for i in range(len(words)):
        chunk = words[i:i + window]
        if not chunk:
            continue
        start = chunk[0][0]
        end = chunk[-1][1
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/gemini_images.py">

```
"""Gemini 3.x image generation client for labeled diagrams and infographics."""
from __future__ import annotations

import base64
from pathlib import Path

from .config import Settings

def generate_diagram(
    prompt: str,
    out_path: Path,
    settings: Settings,
    aspect_ratio: str = "9:16",
    image_size: str = "1K",
    style_hint: str = "",
) -> bool:
    if not settings.gemini_api_key:
        return False

    try:
        from google import genai
    except ImportError:
        print("[gemini-images] google-genai not installed — skipping")
        return False

    client = genai.Client(api_key=settings.gemini_api_key)

    full_prompt = prompt.strip()
    if style_hint:
        full_prompt += f"\n\nVisual style: {style_hint}"
    full_prompt += (
        "\n\nRequirements: vertical 9:16 composition, clean white or off-white "
        "background, no watermarks, no logos, no people. If text is included, "
        "it must be legible and spell-checked. Prefer simple labeled shapes, "
        "arrows, and clear icons over photorealism."
    )

    try:
        interaction = client.interactions.create(
            model=settings.gemini_image_model,
            input=full_prompt,
            response_format={
                "type": "image",
                "mime_type": "image/png",
                "aspect_ratio": aspect_ratio,
                "image_size": image_size,
            },
        )
        image_data = getattr(interaction, "output_image", None)
        if image_data is None or not getattr(image_data, "data", None):
            print("[gemini-images] no image in response")
            return False
        out_path.write_bytes(base64.b64decode(image_data.data))
        if out_path.stat().st_size < 3_000:
            out_path.unlink(missing_ok=True)
            return False
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[gemini-images] failed: {type(exc).__name__}: {exc}")
        return False
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/manim_renderer.py">

```
"""Manim wrapper for programmatic educational animations.

SECURITY: This runs LLM-authored Python. It is NOT a full sandbox. The prompt
restricts to safe Manim primitives and the wrapper double-checks with a regex.
Review the first 5 outputs manually before scaling.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import uuid
from pathlib import Path

from .config import MANIM_MEDIA_DIR

_FORBIDDEN = re.compile(
    r"\b("
    r"import\s+os|import\s+sys|import\s+subprocess|import\s+shutil|"
    r"open\s*\(|__import__|eval\s*\(|exec\s*\(|"
    r"requests\.|urllib|socket|http\.|"
    r"shutil\.|subprocess\.|os\.|sys\."
    r")\b"
)

SCENE_CLASS = "MainScene"

def _scene_preamble() -> str:
    return (
        "from manim import *\n\n"
        "config.pixel_width = 1080\n"
        "config.pixel_height = 1920\n"
        "config.frame_height = 16.0\n"
        "config.frame_width = 9.0\n"
        "config.background_color = WHITE\n"
    )

def _sanitize(code: str) -> str:
    if _FORBIDDEN.search(code):
        raise ValueError("Generated Manim code contains forbidden patterns")
    code = re.sub(r"^.*config\..*$", "", code, flags=re.MULTILINE)
    code = re.sub(r"class\s+(\w+)\s*\(\s*Scene\s*\)", f"class {SCENE_CLASS}(Scene)", code)
    return _scene_preamble() + "\n" + code

def render_manim_scene(scene_code: str, timeout: int = 240) -> Path | None:
    manim_bin = shutil.which("manim")
    if not manim_bin:
        print("[manim] manim binary not found in PATH — skipping")
        return None

    if SCENE_CLASS not in scene_code:
        m = re.search(r"class\s+(\w+)\s*\(\s*Scene\s*\)", scene_code)
        if not m:
            print("[manim] no Scene subclass found")
            return None
        scene_code = scene_code.replace(m.group(1), SCENE_CLASS)

    try:
        code = _sanitize(scene_code)
    except ValueError as exc:
        print(f"[manim] sanitize failed: {exc}")
        return None

    run_id = uuid.uuid4().hex[:8]
    script_path = MANIM_MEDIA_DIR / f"scene_{run_id}.py"
    media_dir = MANIM_MEDIA_DIR / f"out_{run_id}"
    media_dir.mkdir(parents=True, exist_ok=True)
    script_path.write_text(code, encoding="utf-8")

    cmd = [
        manim_bin,
        "-ql",
        "--format=mp4",
        "--disable_caching",
        f"--media_dir={media_dir}",
        str(script_path),
        SCENE_CLASS,
    ]

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(f"[manim] render timed out after {timeout}s")
        return None

    if proc.returncode != 0:
        print(f"[manim] render failed:\n{proc.stderr[-800:]}")
        return None

    mp4s = list(media_dir.rglob(f"{SCENE_CLASS}.mp4"))
    if not mp4s:
        print("[manim] rendered but no MP4 found")
        return None

    return mp4s[0]
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/visuals.py">

```
"""Scene router — sends each scene to the right visual tool.

Routing logic (per scene["visual"]["type"]):

    concept_animation  →  Manim
    diagram            →  Gemini
    infographic        →  Gemini
    formula            →  Gemini
    stock_footage      →  Pexels
    <anything else>    →  Pollinations
"""
from __future__ import annotations

import random
import urllib.parse
from pathlib import Path

import requests

from .config import WORK_DIR, Settings
from .gemini_images import generate_diagram
from .manim_renderer import render_manim_scene

STYLE_HINTS = {
    "diagram": (
        "A clean educational diagram on a soft cream background. "
        "Simple shapes, labeled parts, thin dark outlines, one accent color "
        "(indigo #4d6bfe). Textbook illustration style, not photorealistic."
    ),
    "infographic": (
        "A modern vertical infographic on a soft cream background. "
        "Bold sans-serif headings, simple icons, one or two accent colors. "
        "Numbers and short labels only. Clean and minimal."
    ),
    "formula": (
        "A minimal card showing a single math or chemistry formula. "
        "Formula centered, huge and legible, one accent color. "
        "Off-white background, subtle drop shadow, nothing else."
    ),
}

POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}"
POLLINATIONS_SUFFIX = (
    ", vertical 9:16 composition, clean off-white background, "
    "educational illustration, minimal, no text, no watermark"
)

def _try_gemini(prompt: str, scene_type: str, scene_index: int, settings: Settings) -> Path | None:
    out_path = WORK_DIR / f"scene_{scene_index:02d}.png"
    style_hint = STYLE_HINTS.get(scene_type, STYLE_HINTS["diagram"])
    if generate_diagram(prompt, out_path, settings, style_hint=style_hint):
        print(f"[visuals] scene {scene_index}: gemini ({scene_type})")
        return out_path
    return None

def _try_manim(scene_code: str, scene_index: int, settings: Settings) -> Path | None:
    if not settings.manim_enabled or not scene_code:
        return None
    mp4 = render_manim_scene(scene_code)
    if mp4 is None:
        return None
    target = WORK_DIR / f"scene_{scene_index:02d}.mp4"
    target.write_bytes(mp4.read_bytes())
    print(f"[visuals] scene {scene_index}: manim")
    return target

def _try_pexels(query: str, scene_index: int, settings: Settings) -> Path | None:
    if not settings.pexels_api_key:
        return None
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": settings.pexels_api_key},
            params={"query": query, "orientation": "portrait", "per_page": 1, "size": "large"},
            timeout=45,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if not photos:
            return None
        src = photos[0]["src"]["large2x"]
        img = requests.get(src, timeout=90)
        img.raise_for_status()
        out_path = WORK_DIR / f"scene_{scene_index:02d}.jpg"
        out_path.write_bytes(img.content)
        print(f"[visuals] scene {scene_index}: pexels")
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"[visuals] pexels failed: {exc}")
        return None

def _try_pollinations(prompt: str, scene_index: int) -> Path | None:
    try:
        encoded = urllib.parse.quote(prompt + POLLINATIONS_SUFFIX, safe="")
        r = requests.get(
            POLLINATIONS.format(prompt=encoded),
            params={
                "width": 1080,
                "height": 1920,
                "nologo": "true",
                "model": "flux",
                "seed": random.randint(1, 2_000_000_000),
            },
            timeout=120,
        )
        r.raise_for_status()
        if len(r.content) < 5_000:
            return None
        out_path = WORK_DIR / f"scene_{scene_index:02d}.jpg"
        out_path.write_bytes(r.content)
        print(f"[visuals] scene {scene_index}: pollinations (fallback)")
        return out_path
    except Exception as exc:  # noqa: BLE001
        print(f"[visuals] pollinations failed: {exc}")
        return None

def fetch_scene_visual(scene_index: int, visual: dict, settings: Settings) -> Path:
    vtype = (visual.get("type") or "diagram").lower()
    prompt = (visual.get("prompt") or "").strip()
    manim_code = visual.get("manim_code") or ""

    if not prompt and vtype != "concept_animation":
        prompt = "abstract educational concept on a soft cream background"

    if vtype == "concept_animation":
        result = _try_manim(manim_code, scene_index, settings)
        if result:
            return result
        result = _try_gemini(prompt or "concept illustration", "diagram", scene_index, settings)
        if result:
            return result
        result = _try_pollinations(prompt or "concept illustration", scene_index)
        if result:
            return result

    elif vtype in ("diagram", "infographic", "formula"):
        result = _try_gemini(prompt, vtype, scene_index, settings)
        if result:
            return result
        result = _try_pollinations(prompt, scene_index)
        if result:
            return result

    elif vtype == "stock_footage":
        result = _try_pexels(prompt, scene_index, settings)
        if result:
            return result
        result = _try_gemini(prompt, "diagram", scene_index, settings)
        if result:
            return result
        result = _try_pollinations(prompt, scene_index)
        if result:
            return result

    else:
        result = _try_gemini(prompt, "diagram", scene_index, settings)
        if result:
            return result
        result = _try_pollinations(prompt, scene_index)
        if result:
            return result

    raise RuntimeError(f"All visual providers failed for scene {scene_index}")
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/render.py">

```
"""FFmpeg assembly — handles both still images (Ken Burns) and MP4 clips."""
from __future__ import annotations

import subprocess
from pathlib import Path

from .config import WORK_DIR, OUT_DIR, ASSETS_DIR

W, H = 1080, 1920
FPS = 30

def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}):\n{' '.join(cmd)}\n\n"
            f"--- stderr tail ---\n{proc.stderr[-2500:]}"
        )

def _ken_burns_clip(image: Path, duration: float, out: Path, direction: int = 1) -> None:
    total_frames = max(int(duration * FPS), 1)

    if direction % 2 == 0:
        z_expr = "min(1.0+0.0018*on,1.18)"
        y_expr = f"ih/2-(ih/zoom/2)+{int(H*0.03)}*on/{total_frames}"
    else:
        z_expr = "max(1.18-0.0018*on,1.0)"
        y_expr = f"ih/2-(ih/zoom/2)-{int(H*0.03)}*on/{total_frames}"

    x_expr = "iw/2-(iw/zoom/2)"

    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={W}x{H}:fps={FPS},"
        f"format=yuv420p"
    )

    _run([
        "ffmpeg", "-y", "-loop", "1", "-i", str(image),
        "-vf", vf, "-t", f"{duration:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-an", str(out),
    ])

def _conform_video_clip(video:
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/quota.py">

```
"""Tracks daily YouTube quota usage per rotating project.

YouTube Data API v3 free tier = 10,000 units/day.
Each video upload = 1,600 units → 6 uploads per project per day.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT

STATE_PATH = ROOT / ".quota_state.json"
UPLOAD_COST = 1600
DAILY_QUOTA = 100
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/upload.py">

```
"""YouTube Data API v3 uploader with multi-project rotation."""
from __future__ import annotations

from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import YouTubeCredentials
from .quota import pick_project, record_upload
from .script_gen import Script

def _creds_from_payload(payload: dict) -> Credentials:
    return Credentials(
        token=payload.get("token"),
        refresh_token=payload["refresh_token"],
        token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=payload.get("scopes", ["https://www.googleapis.com/auth/youtube.upload"]),
    )

def upload_video(
    video_path
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/pipeline/generate.py">

```
"""Main orchestrator — script → TTS → visuals → render → upload."""
from __future__ import annotations

import argparse
import json
import re
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/scripts/setup_oauth.py">

```
#!/usr/bin/env python3
"""One-time YouTube OAuth setup.

Usage:
    python scripts/setup_oauth.py /path/to/client_secrets.json

Prints a BASE64 blob to paste into GitHub Secrets as YT_CREDS_1.
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OUT_DIR = Path(__file__).resolve().parent.parent / "credentials"

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/setup_oauth.py <client_secrets.json>")
        sys.exit(1)

    client_secrets = Path(sys.argv[1])
    if not client_secrets.exists():
        print(f"[!] Client secrets file not found: {client_secrets}")
        sys.exit(1)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    token_path = OUT_DIR / "token.json"

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), SCOPES)
    creds = flow.run_local_server(port=8080, access_type="offline", prompt="consent")

    with open(client_secrets) as f:
        cfg = json.load(f)
    installed = cfg.get("installed") or cfg.get("web") or {}

    token_payload = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "universe_domain": installed.get("universe_domain", "googleapis.com"),
    }

    token_path.write_text(json.dumps(token_payload, indent=2))
    print(f"\n[✓] Token saved to {token_path}")

    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        resp = yt.channels().list(part="snippet", mine=True).execute()
        items = resp.get("items", [])
        if items:
            print(f"[✓] Authenticated as channel: {items[0]['snippet']['title']}")
        else:
            print("[!] No channel found
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/scripts/encode_creds.py">

```
#!/usr/bin/env python3
"""Encode an existing token JSON into base64 for GitHub Secrets.

Usage:
    python scripts/encode_creds.py credentials/token.json
"""
from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/encode_creds.py <token.json>")
        sys.exit(1)
    p = Path(sys.argv[1])
    data = json.loads(p.read_text())
    b64 = base64.b64encode(json.dumps(data).encode()).decode()
    print(b64)

if __name__ == "__main__":
    main()
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/tts-server/Dockerfile">

```
# Chatterbox TTS server — CPU-only image for GitHub Actions sidecar.
#
# Built by .github/workflows/build-tts.yml and pushed to GHCR.
# Pulled at the start of every pipeline run.
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      build-essential \
      libsndfile1 \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/chatterbox

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip wheel && \
    pip install --no-cache-dir \
      --extra-index-url https://download.pytorch.org/whl/cpu \
      -r requirements.txt

# Pre-download base Chatterbox weights so container start is fast.
RUN python -c "from chatterbox.tts_turbo import ChatterboxTurboTTS; \
    ChatterboxTurboTTS.from_pretrained(device='cpu')" || \
    echo "[warn] model preload failed — will download on first request"

COPY server.py .
COPY references/ /opt/chatterbox/references/

ENV CHATTERBOX_DEVICE=cpu \
    CHATTERBOX_MODEL_DIR=/opt/chatterbox/models \
    CHATTERBOX_REFERENCE_DIR=/opt/ch
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/tts-server/requirements.txt">

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
chatterbox-tts
torch>=2.2.0
torchaudio>=2.2.0
numpy
soundfile
huggingface-hub
python-multipart
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/tts-server/docker-compose.yml">

```
services:
  chatterbox:
    build: .
    image: chatterbox-tts:local
    ports:
      - "8000:8000"
    environment:
      - CHATTERBOX_DEVICE=cpu
      - TTS_SERVER_TOKEN=${TTS_SERVER_TOKEN:-}
    volumes:
      - ./
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/tts-server/server.py">

```
#!/usr/bin/env python3
"""Chatterbox TTS HTTP server — container-ready."""
from __future__ import annotations

import io
import logging
import os
import threading
```

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/tts-server/references/README.md">

```
# Reference Voice Clips

Drop 5-10 second WAV clips here to enable voice cloning:

- `hindi_female.wav`
- `hindi_male.wav`

If missing, the server falls back to Chatterbox's built-in voice.

## Requirements

- Format: 
```

After adding clips, re-run the **Build TTS Docker Image** GitHub workflow.

</BDS:create_file>

<BDS:create

</BDS:create_file>

<BDS:create_file fileName="youtube-automation/.github/workflows/build-tts.yml">

```yaml
name: Build TTS Docker Image

on:
  push:
    branches: [main]
    paths:
      - 'tts-server

