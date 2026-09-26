"""High-quality 9:16 FFmpeg video assembly without subtitles."""
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
            f"--- stderr tail ---\n{proc.stderr[-3500:]}"
        )


def _ken_burns_clip(image: Path, duration: float, out: Path, direction: int = 1) -> None:
    total_frames = max(int(duration * FPS), 1)

    if direction % 2 == 0:
        z_expr = "min(1.0+0.0017*on,1.16)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)+{int(H*0.025)}*on/{total_frames}"
    else:
        z_expr = "max(1.16-0.0017*on,1.0)"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)-{int(H*0.025)}*on/{total_frames}"

    vf = (
        f"scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
        f"crop={W*2}:{H*2},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={total_frames}:s={W}x{H}:fps={FPS},"
        "eq=contrast=1.035:saturation=1.06:brightness=0.005,"
        "unsharp=5:5:0.30:5:5:0,"
        "format=yuv420p"
    )

    _run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", str(image),
        "-vf", vf,
        "-t", f"{duration:.3f}",
        "-r", str(FPS),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        str(out),
    ])


def _concat_clips(clips: list[Path], out: Path) -> None:
    listfile = WORK_DIR / "concat.txt"
    listfile.write_text(
        "\n".join(f"file '{c.resolve()}'" for c in clips),
        encoding="utf-8",
    )
    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(listfile),
        "-c", "copy",
        str(out),
    ])


def _concat_audio(paths: list[Path], out: Path) -> None:
    listfile = WORK_DIR / "audio_concat.txt"
    listfile.write_text(
        "\n".join(f"file '{p.resolve()}'" for p in paths),
        encoding="utf-8",
    )
    _run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(listfile),
        "-c:a", "libmp3lame",
        "-b:a", "192k",
        str(out),
    ])


def _pick_music() -> Path | None:
    music_dir = ASSETS_DIR / "music"
    if not music_dir.exists():
        return None
    tracks = sorted(music_dir.glob("*.mp3")) + sorted(music_dir.glob("*.m4a"))
    return tracks[0] if tracks else None


def assemble_video(
    scene_images: list[Path],
    scene_audios: list[Path],
    scene_ass: list[Path] | None,
    scene_durations: list[float],
    slug: str,
) -> Path:
    """Assemble narration + visuals.

    scene_ass is retained only for API compatibility. It is never read and
    never passed to FFmpeg. V5 intentionally has zero subtitles.
    """
    del scene_ass
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if not scene_images or not scene_audios:
        raise ValueError("Cannot render video without scenes")

    clips: list[Path] = []
    for i, (img, dur) in enumerate(zip(scene_images, scene_durations)):
        clip = WORK_DIR / f"clip_{i:02d}.mp4"
        _ken_burns_clip(img, max(dur + 0.15, 1.0), clip, direction=i)
        clips.append(clip)

    silent_video = WORK_DIR / "silent_video.mp4"
    _concat_clips(clips, silent_video)

    voice = WORK_DIR / "voice.mp3"
    _concat_audio(scene_audios, voice)

    final = OUT_DIR / f"{slug}.mp4"
    music = _pick_music()

    common_video = [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-profile:v", "high",
        "-level", "4.2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
    ]

    if music:
        filter_complex = (
            "[1:a]volume=1.0[voice];"
            "[2:a]volume=0.045,aloop=loop=-1:size=2e9[music];"
            "[voice][music]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(silent_video),
            "-i", str(voice),
            "-i", str(music),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            *common_video,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(final),
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(silent_video),
            "-i", str(voice),
            "-map", "0:v", "-map", "1:a",
            *common_video,
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(final),
        ]

    _run(cmd)

    if not final.exists() or final.stat().st_size < 100_000:
        raise RuntimeError("Final video was not produced correctly")

    return final
