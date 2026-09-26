#!/usr/bin/env python3
"""HTTP server wrapping the YouTube Shorts pipeline.

The Cloudflare Worker sends a POST /generate request with a JSON body:
    {
      "niche": "facts",
      "topic": "octopus has three hearts",
      "language": "en",
      "GEMINI_API_KEY": "...",
      "PEXELS_API_KEY": "..."
    }

The server runs the full pipeline (script → TTS → visuals → render),
base64-encodes the resulting MP4, and returns:
    { "success": true, "videoBase64": "...", "script": {...} }
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Add the pipeline package to the path.
sys.path.insert(0, str(Path(__file__).parent))

from pipeline.config import WORK_DIR, OUT_DIR, ensure_dirs
from pipeline.render import assemble_video
from pipeline.script_gen import generate_script
from pipeline.tts import synthesize_scene
from pipeline.visuals import fetch_scene_image

app = FastAPI()

def _slug(text: str, max_len: int = 50) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    s = re.sub(r"[\s_-]+", "-", s).strip("-")
    return s[:max_len] or "video"

@app.get("/health")
async def health():
    return {"ok": True, "ts": datetime.now(timezone.utc).isoformat()}

@app.post("/generate")
async def generate(request: Request):
    ensure_dirs()

    try:
        payload = await request.json()
    except Exception as e:
        return JSONResponse(
            {"success": False, "error": f"Invalid JSON body: {e}"},
            status_code=400,
        )

    niche = payload.get("niche", "facts")
    topic = payload.get("topic", "")
    language = payload.get("language", "en")

    if not topic:
        return JSONResponse(
            {"success": False, "error": "Missing 'topic' in payload."},
            status_code=400,
        )

    # Build a minimal settings object from the payload secrets.
    class _Settings:
        gemini_api_key = payload.get("GEMINI_API_KEY")
        openrouter_api_key = payload.get("OPENROUTER_API_KEY")
        pexels_api_key = payload.get("PEXELS_API_KEY")
        pixabay_api_key = payload.get("PIXABAY_API_KEY")
        youtube_projects = []
        upload_enabled = False  # the Worker handles upload
        niches_enabled = [niche]
        languages_enabled = [language]

    settings = _Settings()

    # TTS providers read these from the process environment. The Worker passes
    # them as job-scoped secrets/config so Cloudflare and GitHub use the same
    # pipeline behavior.
    for key in ("TTS_PROVIDER", "TTS_LANGUAGE", "TTS_VOICE", "TTS_NO_FALLBACK"):
        if key in payload and payload[key]:
            os.environ[key] = str(payload[key])

    if not settings.gemini_api_key and not settings.openrouter_api_key:
        return JSONResponse(
            {"success": False, "error": "No LLM API key provided."},
            status_code=400,
        )

    # Load the niche config from the content plan. The Worker passes the
    # full content plan in the payload so the container doesn't need R2 access.
    content_plan = payload.get("content_plan", {})
    niche_cfg = content_plan.get(niche, {
        "visual_style": "text_gradient_ai",
        "voice": {"en": "en-IN"},
        "system_prompt": "You are a knowledgeable Indian exam teacher creating helpful educational videos.",
    })

    try:
        # 1. Script
        print(f"[server] Generating script for: {topic!r}")
        script = generate_script(topic, niche_cfg, language, settings)

        # 2. TTS + visuals per scene
        voice = niche_cfg.get("voice", {}).get(language, "en-US-GuyNeural")
        visual_style = niche_cfg.get("visual_style", "text_gradient_ai")

        scene_images: list[Path] = []
        scene_audios: list[Path] = []
        scene_ass: list[Path] = []
        durations: list[float] = []

        for scene in script.scenes:
            print(f"[server] Scene {scene.index + 1}/{len(script.scenes)}")
            audio, ass, dur = synthesize_scene(
                subtitle_text=scene.narration,
                tts_text=scene.tts_text,
                voice=voice,
                audio_path=WORK_DIR / f"scene_{scene.index:02d}.mp3",
                ass_path=None,
                overlay_text=None,
            )
            img = fetch_scene_image(scene.index, scene.image_prompt, visual_style, settings)
            scene_images.append(img)
            scene_audios.append(audio)
            scene_ass.append(ass)
            durations.append(dur)

        # 3. Render
        print("[server] Assembling video...")
        slug = _slug(script.title)
        video_path = assemble_video(scene_images, scene_audios, scene_ass, durations, slug)

        # 4. Base64-encode the MP4 for transport back to the Worker.
        video_bytes = video_path.read_bytes()
        video_b64 = base64.b64encode(video_bytes).decode("ascii")

        print(f"[server] Done: {len(video_bytes) / 1024 / 1024:.1f} MB")

        return {
            "success": True,
            "videoBase64": video_b64,
            "script": script.to_dict(),
        }

    except Exception as e:
        tb = traceback.format_exc()
        print(f"[server] FAILED: {e}\n{tb}", file=sys.stderr)
        return JSONResponse(
            {"success": False, "error": f"{type(e).__name__}: {e}"},
            status_code=500,
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080, log_level="info")
