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
        scopes=payload.get(
            "scopes",
            ["https://www.googleapis.com/auth/youtube.upload"],
        ),
    )

def upload_video(
    video_path: Path,
    script: Script,
    projects: list[YouTubeCredentials],
    privacy: str = "public",
    category_id: str = "27",
) -> dict:
    names = [p.name for p in projects]
    chosen_name, state = pick_project(names)
    if chosen_name is None:
        raise RuntimeError(
            "All YouTube projects have exhausted their daily quota "
            "(10,000 units each; 1,600 per upload = 6 uploads/day each)."
        )

    chosen = next(p for p in projects if p.name == chosen_name)
    creds = _creds_from_payload(chosen.payload)
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body = {
        "snippet": {
            "title": script.title[:100],
            "description": script.description[:4900],
            "tags": script.tags[:15],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        mimetype="video/mp4",
        resumable=True,
        chunksize=4 * 1024 * 1024,
    )

    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        _, response = request.next_chunk()

    record_upload(chosen.name, state)

    return {
        "video_id": response["id"],
        "url": f"https://youtu.be/{response['id']}",
        "project": chosen.name,
    }
