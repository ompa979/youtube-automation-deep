"""Deterministic content QA for the natural ExamCracker Shorts pipeline."""
from __future__ import annotations

import re
from dataclasses import dataclass

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .script_gen import Script

HINDI_MARKERS = {
    "hai", "hain", "kyun", "kyon", "kyunki", "ka", "ke", "ki", "ko", "se", "me", "mein",
    "aur", "lekin", "agar", "toh", "to", "ye", "yeh", "woh", "iska", "iske", "uska", "uske",
    "aap", "hum", "dekho", "samjho", "socho", "matlab", "sirf", "bhi", "jab", "jabki", "phir",
    "ek", "do", "kya", "kaise", "kyonki", "hota", "hote", "hoti", "karna", "karte", "liye",
    "yaad", "rakho", "exam", "question", "answer", "important", "reason", "wajah",
}
GENERIC_FILLERS = [
    "hello everyone", "welcome back", "guys aaj", "today we are going to",
    "in this video we will", "don't forget to subscribe",
]

@dataclass
class QAResult:
    ok: bool
    issues: list[str]


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text.lower()))


def validate_script(script: Any, language: str) -> QAResult:
    issues: list[str] = []
    all_text = " ".join([script.title, script.hook] + [s.narration for s in script.scenes]).strip()
    words = _word_count(all_text)

    if not script.title.strip():
        issues.append("missing title")
    if not script.hook.strip():
        issues.append("missing hook")
    if not (3 <= len(script.scenes) <= 8):
        issues.append(f"scene count {len(script.scenes)} outside natural range 3-8")
    if words < 25:
        issues.append("explanation is too thin to teach the concept; add the missing reasoning")
    if words > 500:
        issues.append("script is too long for a Short-format video; simplify only redundant material, never the core explanation")

    if any("\u0900" <= ch <= "\u097F" for ch in all_text):
        issues.append("English-only pipeline: Devanagari/Hindi text detected")

    for i, scene in enumerate(script.scenes, start=1):
        if not scene.narration.strip():
            issues.append(f"scene {i}: empty narration")
        if not scene.image_prompt.strip():
            issues.append(f"scene {i}: missing visual prompt")
        if len(scene.on_screen_text.split()) > 8:
            issues.append(f"scene {i}: too much on-screen text")
        if any(x in scene.narration.lower() for x in GENERIC_FILLERS):
            issues.append(f"scene {i}: generic AI filler/opening")

    if language != "en":
        issues.append(f"V5 requires language=en; received {language!r}")

    # We intentionally do not enforce a duration or word count target beyond
    # sanity limits. Audio length should emerge from the teaching content.
    return QAResult(ok=not issues, issues=issues)
