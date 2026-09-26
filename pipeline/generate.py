"""End-to-end GitHub Actions YouTube Shorts pipeline.

Generates one video:
topic rotation -> Gemini/OpenRouter script -> Edge-TTS -> visuals -> FFmpeg
and optionally uploads it to YouTube.

The Cloudflare/container path uses the same lower-level pipeline modules, while
this entrypoint provides the missing GitHub Actions entrypoint.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
from pathlib import Path
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from .config import Settings, YouTubeCredentials, CONTENT_PLAN_PATH, WORK_DIR, OUT_DIR, ensure_dirs
from .render import assemble_video
from .script_gen import generate_script
from .tts import synthesize_scene
from .visuals import fetch_scene_image

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / ".quota_state.json"


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _csv_env(name: str, default: list[str]) -> list[str]:
    raw = os.getenv(name)
    if not raw:
        return default
    return [x.strip() for x in raw.split(",") if x.strip()]


def _decode_credential(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        raise ValueError("empty credential")
    try:
        return json.loads(base64.b64decode(raw).decode("utf-8"))
    except Exception:
        return json.loads(raw)


def _load_settings() -> Settings:
    creds: list[YouTubeCredentials] = []
    for i in range(1, 20):
        raw = os.getenv(f"YT_CREDS_{i}")
        if not raw:
            continue
        try:
            creds.append(YouTubeCredentials(i, _decode_credential(raw)))
        except Exception as exc:
            print(f"[!] Ignoring invalid YT_CREDS_{i}: {exc}")

    return Settings(
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY"),
        pexels_api_key=os.getenv("PEXELS_API_KEY"),
        pixabay_api_key=os.getenv("PIXABAY_API_KEY"),
        youtube_projects=creds,
        upload_enabled=_truthy(os.getenv("UPLOAD_ENABLED"), True),
        niches_enabled=_csv_env("NICHES_ENABLED", ["facts"]),
        languages_enabled=_csv_env("LANGUAGES_ENABLED", ["en"]),
    )


def _load_plan() -> dict[str, Any]:
    if not CONTENT_PLAN_PATH.exists():
        raise FileNotFoundError(f"Missing content plan: {CONTENT_PLAN_PATH}")
    data = json.loads(CONTENT_PLAN_PATH.read_text(encoding="utf-8"))
    return data.get("niches", data)


def _load_state() -> dict[str, int]:
    if not STATE_PATH.exists():
        return {"niche": 0, "language": 0, "topic": 0, "project": 0}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        return {
            "niche": int(data.get("niche", 0)),
            "language": int(data.get("language", 0)),
            "topic": int(data.get("topic", 0)),
            "project": int(data.get("project", 0)),
        }
    except Exception:
        return {"niche": 0, "language": 0, "topic": 0, "project": 0}


def _save_state(state: dict[str, int]) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _slug(text: str, max_len: int = 60) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:max_len] or "youtube-short"


def _choose(plan: dict[str, Any], settings: Settings, state: dict[str, int]):
    available_niches = [
        n for n in settings.niches_enabled
        if n in plan and plan[n].get("topics")
    ]
    if not available_niches:
        raise RuntimeError(
            f"No enabled niches have topics. Enabled={settings.niches_enabled}; "
            f"available={list(plan)}"
        )

    niche = available_niches[state["niche"] % len(available_niches)]
    cfg = plan[niche]

    languages = [
        lang for lang in settings.languages_enabled
        if lang in cfg.get("voice", {})
    ]
    if not languages:
        raise RuntimeError(
            f"No enabled languages available for niche '{niche}'. "
            f"Enabled={settings.languages_enabled}; voices={list(cfg.get('voice', {}))}"
        )

    language = languages[state["language"] % len(languages)]
    topics = cfg["topics"]
    topic = topics[state["topic"] % len(topics)]

    # Advance independently so each successful run moves the rotation.
    state["niche"] += 1
    state["language"] += 1
    state["topic"] += 1
    return niche, cfg, language, topic


def _upload(video_path: Path, script, cred: YouTubeCredentials) -> str:
    payload = cred.payload
    credentials = Credentials(
        token=payload.get("token"),
        refresh_token=payload["refresh_token"],
        token_uri=payload.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=payload["client_id"],
        client_secret=payload["client_secret"],
        scopes=payload.get("scopes", ["https://www.googleapis.com/auth/youtube.upload"]),
    )

    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    body = {
        "snippet": {
            "title": script.title[:100],
            "description": script.description[:4900],
            "tags": script.tags[:15],
            "categoryId": "27",
        },
        "status": {
            "privacyStatus": "public",
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=8 * 1024 * 1024,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"[upload] {int(status.progress() * 100)}%")

    video_id = response["id"]
    return f"https://youtu.be/{video_id}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="render the video but never upload")
    args = parser.parse_args()

    ensure_dirs()
    settings = _load_settings()
    plan = _load_plan()
    state = _load_state()

    if not settings.openrouter_api_key and not settings.gemini_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is required (GEMINI_API_KEY is optional fallback)")

    niche, niche_cfg, language, topic = _choose(plan, settings, state)
    print(f"[pipeline] niche={niche} language={language} topic={topic}")

    script = generate_script(topic, niche_cfg, language, settings)
    print(f"[pipeline] title={script.title!r}; scenes={len(script.scenes)}")

    voice = niche_cfg.get("voice", {}).get(language, "en-IN")
    visual_style = niche_cfg.get("visual_style", "text_gradient_ai")

    scene_images = []
    scene_audios = []
    scene_ass = []  # compatibility only; V5 render ignores subtitles
    durations = []

    scene_dir = WORK_DIR / "scenes"
    scene_dir.mkdir(parents=True, exist_ok=True)

    for scene in script.scenes:
        scene_no = scene.index + 1
        print(f"[pipeline] scene {scene_no}/{len(script.scenes)}")

        narration = (scene.narration or "").strip()
        tts_text = (scene.tts_text or narration).strip()
        if not narration:
            raise ValueError(
                f"Scene {scene_no} has empty narration after script normalization"
            )
        if not tts_text:
            tts_text = narration

        audio_path = scene_dir / f"scene_{scene.index:02d}.mp3"
        ass_path = scene_dir / f"scene_{scene.index:02d}.ass"

        audio, ass, duration = synthesize_scene(
            subtitle_text=narration,
            tts_text=tts_text,
            voice=voice,
            audio_path=audio_path,
            ass_path=ass_path,
            overlay_text=None,
        )

        image = fetch_scene_image(
            scene.index, scene.image_prompt, visual_style, settings
        )
        scene_images.append(image)
        scene_audios.append(audio)
        scene_ass.append(ass)
        durations.append(duration)

    metadata_path = OUT_DIR / "script.json"
    metadata_path.write_text(json.dumps(script.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    video_path = assemble_video(
        scene_images,
        scene_audios,
        scene_ass,
        durations,
        _slug(script.title),
    )
    print(f"[pipeline] rendered={video_path} "
          f"size={video_path.stat().st_size / 1024 / 1024:.1f} MB")

    if args.dry_run:
        print("[i] Dry run — skipping upload.")
        _save_state(state)
        return 0

    if not settings.upload_enabled:
        print("[i] UPLOAD_ENABLED is disabled — skipping upload.")
        _save_state(state)
        return 0

    if not settings.youtube_projects:
        raise RuntimeError(
            "Upload requested but no YT_CREDS_N secrets are configured."
        )

    # Rotate credentials on successful uploads. If one project fails, try the
    # next configured project before failing the workflow.
    start = state["project"] % len(settings.youtube_projects)
    last_error: Exception | None = None

    for offset in range(len(settings.youtube_projects)):
        idx = (start + offset) % len(settings.youtube_projects)
        cred = settings.youtube_projects[idx]
        try:
            url = _upload(video_path, script, cred)
            state["project"] = idx + 1
            _save_state(state)
            print(f"[✓] Uploaded with YT_CREDS_{cred.index}: {url}")
            return 0
        except Exception as exc:
            last_error = exc
            print(f"[!] YT_CREDS_{cred.index} failed: {exc}")

    raise RuntimeError(f"All YouTube credentials failed: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
