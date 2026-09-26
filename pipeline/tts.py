"""Free Indian-English narration backend for the YouTube Shorts pipeline.

Policy for V5:
- narration is English only
- no subtitles / ASS files are generated or rendered
- gTTS with Google's India endpoint is the primary free voice
- Edge is intentionally not used because its public websocket endpoint can return
  403 in GitHub-hosted runners
- espeak-ng en-in is the final offline fallback
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

try:
    from gtts import gTTS
except ImportError:
    gTTS = None


TTS_PROVIDER = "gtts"
TTS_LANGUAGE = "en-IN"
# Kept for workflow/config compatibility. gTTS selects the India endpoint
# through tld=co.in rather than a Microsoft voice name.
TTS_VOICE = "en-IN"
TTS_NO_FALLBACK = False


def _clean_text(text: str) -> str:
    return " ".join(str(text or "").replace("\r", " ").replace("\n", " ").split()).strip()


def _probe_duration(path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        value = result.stdout.strip()
        return max(0.0, float(value)) if value else 0.0
    except (ValueError, OSError):
        return 0.0


def _estimate_duration(text: str) -> float:
    words = max(1, len(_clean_text(text).split()))
    return max(1.5, words / 2.35)


def _gtts_synth(text: str, audio_path: Path) -> float:
    if gTTS is None:
        raise RuntimeError("gTTS is not installed")

    # lang=en + tld=co.in gives Google's India English endpoint without
    # requiring Google Cloud billing or credentials.
    tts = gTTS(
        text=text,
        lang="en",
        tld="co.in",
        slow=False,
    )
    tts.save(str(audio_path))

    if not audio_path.exists() or audio_path.stat().st_size < 1000:
        raise RuntimeError("gTTS produced no usable audio")

    return _probe_duration(audio_path) or _estimate_duration(text)


def _espeak_synth(text: str, audio_path: Path) -> float:
    executable = shutil.which("espeak-ng") or shutil.which("espeak")
    if not executable:
        raise RuntimeError("espeak-ng/espeak is not installed")

    # Prefer Indian English if the runner has it; espeak will reject it on
    # installations without the voice, so retry with en.
    last_error = None
    for voice in ("en-in", "en"):
        try:
            result = subprocess.run(
                [executable, "-v", voice, "-s", "165", "-w", str(audio_path), text],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0 and audio_path.exists() and audio_path.stat().st_size > 1000:
                return _probe_duration(audio_path) or _estimate_duration(text)
            last_error = result.stderr.strip() or f"exit {result.returncode}"
        except Exception as exc:
            last_error = str(exc)

    raise RuntimeError(f"eSpeak Indian-English fallback failed: {last_error}")


def synthesize_scene(
    subtitle_text: str | None = None,
    tts_text: str | None = None,
    voice: str | None = None,
    audio_path: str | Path | None = None,
    ass_path: str | Path | None = None,
    overlay_text: str | None = None,
    *args,
    **kwargs,
):
    """Create narration audio.

    The old subtitle arguments are accepted for backward compatibility, but
    deliberately ignored. The function returns (audio_path, None, duration)
    so older callers do not break while render.py guarantees that no ASS
    subtitle filter is used.
    """
    # Support the old positional call:
    # synthesize_scene(scene_index, narration, tts_text, voice, on_screen_text)
    if isinstance(subtitle_text, int):
        positional = [subtitle_text, tts_text, voice, audio_path, ass_path, overlay_text, *args]
        narration = positional[1] if len(positional) > 1 else ""
        spoken = positional[2] if len(positional) > 2 else narration
        tts_text = spoken
        subtitle_text = narration

    spoken = _clean_text(tts_text or subtitle_text or "")
    if not spoken:
        raise ValueError("Cannot synthesize empty text")

    if audio_path is None:
        raise ValueError("audio_path is required")

    audio_path = Path(audio_path)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        audio_path.unlink()
    except FileNotFoundError:
        pass

    errors: list[str] = []

    # Free Indian-English chain. gTTS is deliberately first.
    try:
        print("[tts] provider=gtts voice=Google India English (en/co.in)")
        duration = _gtts_synth(spoken, audio_path)
        return audio_path, None, duration
    except Exception as exc:
        errors.append(f"gtts: {exc}")
        print(f"[tts] gTTS failed: {exc}")

    try:
        print("[tts] provider=espeak voice=en-in")
        duration = _espeak_synth(spoken, audio_path)
        return audio_path, None, duration
    except Exception as exc:
        errors.append(f"espeak: {exc}")
        print(f"[tts] eSpeak failed: {exc}")

    raise RuntimeError("All free Indian-English TTS providers failed: " + " | ".join(errors))
