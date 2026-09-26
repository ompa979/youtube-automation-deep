"""Natural Indian-English educational script generation.

OpenRouter is the primary LLM and Gemini is the fallback. The V5 pipeline is
English-only: narration is conversational Indian English, while technical
terms remain in standard English. No subtitles are generated.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, asdict

import google.generativeai as genai
import requests

from .quality import validate_script

@dataclass
class Scene:
    index: int
    narration: str
    tts_text: str
    image_prompt: str
    on_screen_text: str

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


def _build_prompt(topic: str, niche_cfg: dict, language: str, repair: str | None = None) -> str:
    lang_instruction = """
Write the viewer-facing narration in clear, natural conversational Indian English.
Use an Indian English speaking style: simple phrasing, natural rhythm, familiar
Indian examples where useful, but do NOT use Hinglish, Roman Hindi, Devanagari,
or forced Indian slang. Keep technical terms in standard English.
The `tts_text` must be the same English spoken content, optimized only for natural
speech pauses and pronunciation. Do not translate it into Hindi.
""".strip()

    repair_text = f"\nREPAIR REQUEST:\n{repair}\n" if repair else ""
    return f"""
{niche_cfg.get('system_prompt', '')}

You are an excellent Indian exam teacher and educational creator.

{lang_instruction}

TOPIC: {topic}

PRIMARY GOAL: learner value, clarity, factual accuracy and natural delivery.
Do NOT optimize for a fixed duration. Do NOT shorten an explanation merely to
fit a target number of seconds. Do NOT add filler to make it longer. Let the
concept determine how much narration is needed.

CONTENT RULES:
- Teach ONE coherent idea well.
- Start naturally with a question, surprising observation, problem, or useful exam connection. Do not use the same hook pattern repeatedly.
- Explain the mechanism or reasoning, not just the fact.
- Use an analogy or example only when it genuinely improves understanding.
- Introduce difficult technical terms and immediately make them understandable.
- Connect to exam relevance only when it naturally fits the topic.
- A quiz/MCQ is OPTIONAL. Include one only if it improves learning.
- A CTA is OPTIONAL and must never interrupt the explanation.
- Never use generic filler such as 'guys, today we are going to', 'welcome back', or 'don't forget to subscribe'.
- Never invent facts. If a topic is uncertain, explain only what is well-established.

VISUAL RULES:
- Every scene needs an educational visual, not decorative stock imagery.
- The image prompt must describe what should be shown to help the learner understand the narration.
- Prefer diagrams, processes, maps, comparisons, labeled objects, timelines, molecules, arrows, or simple conceptual illustrations when appropriate.
- No text, logos or watermarks inside generated images.
- On-screen text should be a short keyword or memory cue, not a transcript.

Return EXACTLY this JSON shape (no markdown):
{{
  "title": "clear title, under 80 chars, accurate, no fake clickbait",
  "hook": "the first spoken line",
  "description": "2-3 useful sentences with 3 relevant hashtags",
  "tags": ["8-12 lowercase tags"],
  "scenes": [
    {{
      "narration": "natural Indian-English spoken line",
      "tts_text": "same English spoken line optimized for natural TTS",
      "image_prompt": "premium educational visual description, 15-35 words, vertical 9:16, no text, no logos",
      "on_screen_text": ""
    }}
  ]
}}

Use as many scenes as the explanation naturally needs, normally 3-8.
Do not split a sentence just to create more scenes.
{repair_text}
""".strip()


def _extract_json_candidates(raw: str) -> list[str]:
    """Return likely JSON object candidates from an LLM response.

    Models sometimes wrap JSON in markdown or append a short explanation.  We
    scan for balanced JSON objects instead of using rfind("}") because that
    can accidentally include trailing braces/text and produce misleading
    JSONDecodeError messages.
    """
    text = (raw or "").strip()
    candidates: list[str] = []

    # Prefer fenced JSON blocks when present.
    for match in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.I | re.S):
        block = match.group(1).strip()
        if block.startswith("{") and block.endswith("}"):
            candidates.append(block)

    # Scan for balanced object boundaries while respecting quoted strings and
    # escaped quotes. This also handles extra prose before/after the object.
    for start in [m.start() for m in re.finditer(r"\{", text)]:
        depth = 0
        in_string = False
        escaped = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidates.append(text[start:i + 1].strip())
                    break

    # Last-resort legacy slice.
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidates.append(text[first:last + 1])

    # Preserve order but remove duplicates.
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique.append(candidate)
    return unique


def _remove_trailing_commas(text: str) -> str:
    """Remove JSON trailing commas without touching commas inside strings."""
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == ",":
            j = i + 1
            while j < len(text) and text[j].isspace():
                j += 1
            if j < len(text) and text[j] in "}]":
                i += 1
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def _parse_json(raw: str) -> dict:
    candidates = _extract_json_candidates(raw)
    if not candidates:
        raise ValueError(f"No JSON object found in LLM output:\n{(raw or '')[:800]}")

    errors: list[str] = []
    for candidate in candidates:
        try:
            value = json.loads(candidate)
            if isinstance(value, dict):
                return value
            errors.append("JSON root was not an object")
        except json.JSONDecodeError as exc:
            errors.append(f"line {exc.lineno} col {exc.colno}: {exc.msg}")
            try:
                repaired = _remove_trailing_commas(candidate)
                value = json.loads(repaired)
                if isinstance(value, dict):
                    return value
            except json.JSONDecodeError:
                pass

    raise ValueError(
        "OpenRouter returned invalid JSON: " + " | ".join(errors[:4])
        + f"\nRAW:\n{(raw or '')[:1200]}"
    )


def _repair_invalid_json_openrouter(raw: str, original_prompt: str, api_key: str) -> str:
    """Ask OpenRouter to convert malformed model output into strict JSON."""
    repair_prompt = f"""
Return ONLY one valid JSON object matching the schema in the original prompt.
Do not add markdown fences, comments, explanations, or trailing commas.
Preserve the educational content and all required fields. Fix only JSON syntax.

ORIGINAL PROMPT:
{original_prompt}

MALFORMED MODEL OUTPUT:
{raw[:12000]}
""".strip()
    return _generate_openrouter(repair_prompt, api_key)


def _generate_gemini(prompt: str, api_key: str) -> str:
    genai.configure(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
    model = genai.GenerativeModel(model_name, generation_config={
        "temperature": 0.75,
        "response_mime_type": "application/json",
    })
    resp = model.generate_content(prompt)
    text = getattr(resp, "text", None)
    if not text:
        raise RuntimeError("Gemini returned an empty response")
    return text


def _generate_openrouter(prompt: str, api_key: str) -> str:
    endpoint = os.getenv("OPENROUTER_ENDPOINT", "https://openrouter.ai/api/v1/chat/completions")
    configured = os.getenv("OPENROUTER_MODEL")
    models = [configured] if configured else ["openrouter/free", "openai/gpt-oss-120b:free"]
    last_error: Exception | None = None
    for model_name in models:
        if not model_name:
            continue
        try:
            r = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://examcracker-ai.onrender.com",
                    "X-Title": "ExamCracker YouTube Automation",
                },
                json={
                    "model": model_name,
                    "messages": [
                        {"role": "system", "content": "Return only valid JSON. You are an expert Indian educational content creator."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.75,
                    "response_format": {"type": "json_object"},
                },
                timeout=90,
            )
            if not r.ok:
                raise RuntimeError(f"OpenRouter model {model_name} returned HTTP {r.status_code}: {r.text[:800]}")
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError(f"OpenRouter model {model_name} returned empty content")
            return content
        except Exception as exc:
            last_error = exc
    raise RuntimeError(str(last_error) if last_error else "No OpenRouter model configured")


def _first_text(raw: dict, *keys: str) -> str:
    """Return the first non-empty text field from an LLM scene object."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _to_script(data: dict) -> Script:
    raw_scenes = data.get("scenes")
    if not isinstance(raw_scenes, list) or not raw_scenes:
        raise ValueError("Generated script contains no scenes")

    scenes: list[Scene] = []
    for i, raw in enumerate(raw_scenes):
        if not isinstance(raw, dict):
            raise ValueError(f"Scene {i + 1} is not an object")

        # narration is the canonical spoken/caption text.
        # Accept common aliases so an otherwise valid model response cannot
        # silently turn into an empty TTS scene.
        narration = _first_text(
            raw, "narration", "voiceover", "voice_over", "spoken_text",
            "speech", "dialogue", "text", "script",
        )
        tts_text = _first_text(
            raw, "tts_text", "tts", "voice_text", "voiceover", "voice_over",
            "narration", "spoken_text", "speech", "dialogue", "text", "script",
        )

        if not narration and tts_text:
            narration = tts_text
        if not tts_text and narration:
            tts_text = narration

        if not narration:
            raise ValueError(
                f"Generated scene {i + 1} has no usable narration/tts text. "
                f"Available fields: {sorted(raw.keys())}"
            )
        # V5 is English-only. Reject Devanagari so a fallback model cannot
        # silently reintroduce Hindi text into an English voice track.
        if any("\u0900" <= ch <= "\u097F" for ch in narration + tts_text):
            raise ValueError(f"Generated scene {i + 1} contains Devanagari/Hindi text; English-only output required")

        image_prompt = _first_text(
            raw, "image_prompt", "visual_prompt", "visual", "image", "prompt"
        )
        on_screen_text = _first_text(
            raw, "on_screen_text", "caption", "keyword", "memory_cue"
        )

        scenes.append(Scene(
            index=i,
            narration=narration,
            tts_text=tts_text,
            image_prompt=image_prompt,
            on_screen_text=on_screen_text.upper(),
        ))

    return Script(
        title=str(data.get("title", "")).strip(),
        hook=str(data.get("hook", "")).strip(),
        description=str(data.get("description", "")).strip(),
        tags=[str(t).lower().lstrip("#") for t in data.get("tags", [])][:15],
        scenes=scenes,
    )


def generate_script(topic: str, niche_cfg: dict, language: str, settings) -> Script:
    prompt = _build_prompt(topic, niche_cfg, language)
    raw: str | None = None
    errors: list[str] = []

    if settings.openrouter_api_key:
        print("[pipeline] Script generator: OpenRouter (PRIMARY)")
        try:
            raw = _generate_openrouter(prompt, settings.openrouter_api_key)
        except Exception as e:
            errors.append(f"openrouter: {e}")
            print(f"[!] OpenRouter primary failed: {e}")

    if raw is None and settings.gemini_api_key:
        print("[pipeline] Script generator: Gemini (FALLBACK)")
        try:
            raw = _generate_gemini(prompt, settings.gemini_api_key)
        except Exception as e:
            errors.append(f"gemini: {e}")
            print(f"[!] Gemini fallback failed: {e}")

    if raw is None:
        raise RuntimeError(f"All script generators failed: {errors}")

    # Parse the model response separately so malformed JSON can be repaired
    # without throwing away a successful OpenRouter generation.
    try:
        parsed = _parse_json(raw)
    except Exception as parse_exc:
        print(f"[!] Script JSON invalid: {parse_exc}")
        repaired_raw = None
        repair_errors: list[str] = []

        if settings.openrouter_api_key:
            try:
                print("[pipeline] JSON repair: OpenRouter (PRIMARY)")
                repaired_raw = _repair_invalid_json_openrouter(
                    raw, prompt, settings.openrouter_api_key
                )
            except Exception as e:
                repair_errors.append(f"openrouter-json-repair: {e}")
                print(f"[!] OpenRouter JSON repair failed: {e}")

        if repaired_raw is None and settings.gemini_api_key:
            try:
                print("[pipeline] JSON repair: Gemini (FALLBACK)")
                repaired_raw = _generate_gemini(
                    "Convert the following malformed output into ONLY the exact JSON schema requested. "
                    "Do not add markdown or explanations.\n\n" + raw[:12000],
                    settings.gemini_api_key,
                )
            except Exception as e:
                repair_errors.append(f"gemini-json-repair: {e}")

        if repaired_raw is None:
            raise RuntimeError(
                "OpenRouter returned invalid JSON and JSON repair failed: "
                + "; ".join(repair_errors)
            ) from parse_exc

        try:
            parsed = _parse_json(repaired_raw)
            print("[qa] JSON repair succeeded")
        except Exception as repair_parse_exc:
            raise RuntimeError(
                f"OpenRouter returned invalid JSON; repair output was also invalid: {repair_parse_exc}"
            ) from parse_exc

    script = _to_script(parsed)
    qa = validate_script(script, language)
    if qa.ok:
        print(f"[qa] script passed: scenes={len(script.scenes)} words={sum(len(s.narration.split()) for s in script.scenes)}")
        return script

    print("[qa] first script needs repair: " + "; ".join(qa.issues))
    repair_prompt = _build_prompt(topic, niche_cfg, language, "; ".join(qa.issues))
    try:
        if settings.openrouter_api_key:
            print("[pipeline] Script repair: OpenRouter (PRIMARY)")
            raw2 = _generate_openrouter(repair_prompt, settings.openrouter_api_key)
        elif settings.gemini_api_key:
            print("[pipeline] Script repair: Gemini (FALLBACK)")
            raw2 = _generate_gemini(repair_prompt, settings.gemini_api_key)
        else:
            raise RuntimeError("No LLM key available for script repair")
        repaired = _to_script(_parse_json(raw2))
        qa2 = validate_script(repaired, language)
        if not qa2.ok:
            raise RuntimeError("; ".join(qa2.issues))
        print(f"[qa] repaired script passed: scenes={len(repaired.scenes)}")
        return repaired
    except Exception as exc:
        raise RuntimeError(f"Generated script failed QA after one repair attempt: {exc}") from exc
