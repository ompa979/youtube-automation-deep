Replace only .github/workflows/generate.yml with the included file. This fixes the brittle subtitle verification that failed because it expected the internal helper name _safe_text. It now verifies actual ASS Dialogue and caption-style generation, then compiles the pipeline.


V5 BUILD — ENGLISH INDIAN VOICE / NO SUBTITLES
================================================
- Script language: English only.
- TTS: free Google gTTS using the India endpoint (en + co.in).
- TTS fallback: local espeak-ng/en-in if gTTS is unavailable.
- Microsoft Edge TTS is not used because its public websocket can return 403 on GitHub runners.
- Subtitles/ASS are completely disabled in rendering.
- Visuals use premium cinematic prompts and image validation.
- Final video: 1080x1920, 30 FPS, H.264 CRF 18, AAC 192 kbps, faststart.
- OpenRouter remains the PRIMARY script generator; Gemini is fallback.
