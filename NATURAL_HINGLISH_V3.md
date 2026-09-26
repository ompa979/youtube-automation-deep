# ExamCracker Natural Hinglish V3

This build implements the research-led design: educational value first, natural Hinglish, no fixed duration target, OpenRouter primary, resilient TTS, educational visuals, and deterministic script QA.

## Core rules
- The concept determines the runtime. There is no 30/40/60-second word target.
- One coherent learning objective per video.
- Roman Hinglish is used for viewer-facing captions; `tts_text` can use Devanagari for Hindi words so Hindi-capable voices pronounce them naturally.
- English technical terms are retained where they are standard exam vocabulary.
- MCQs and CTAs are optional and are only included when they add value.
- Visual prompts must teach the concept rather than decorate it.

## LLM order
1. OpenRouter (`openrouter/free`, then `openai/gpt-oss-120b:free`)
2. Gemini fallback
3. One QA/repair pass using the same primary/fallback order

## TTS order
1. Edge TTS (`hi-IN-MadhurNeural`)
2. Edge TTS (`hi-IN-SwaraNeural`)
3. gTTS Hindi
4. local eSpeak Hindi

All TTS options are free to use in this pipeline; no Google Cloud TTS credentials are required. The pipeline never speeds up narration to hit a target duration.

## Dry-run artifact
The existing workflow copies `/tmp/out/*.mp4` to `out/` and uploads it as `rendered-video-<run number>` so the generated video can be reviewed before publishing.


## Free Hindi/Hinglish TTS

Default: Edge TTS with `hi-IN-MadhurNeural`, then `hi-IN-SwaraNeural`, then gTTS Hindi, then eSpeak Hindi. Configure `TTS_PROVIDER`, `TTS_LANGUAGE`, and `TTS_VOICE`; no Google Cloud billing or credentials are required.
