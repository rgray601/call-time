"""Question bank. Drawing a question does not spend a token."""

from __future__ import annotations

import json
import random

from app.config import ROOT

QUESTIONS = {
    item["id"]: item
    for item in json.loads((ROOT / "data" / "questions.json").read_text(encoding="utf-8"))
}

FORMATS = {
    "onstage": {"label": "On-stage", "seconds": 25},
    "final": {"label": "Final question", "seconds": 30},
    "private": {"label": "Private interview", "seconds": 90},
}

LANES = {
    "title": "Why you",
    "platform": "Platform",
    "personal": "Personal",
    "hypothetical": "A hard room",
    "current": "In the news",
}


def get_question(question_id: str) -> dict | None:
    item = QUESTIONS.get(question_id)
    return dict(item) if item else None


def draw_question(fmt: str, lane: str | None = None, exclude: str | None = None) -> dict:
    if fmt not in FORMATS:
        fmt = "onstage"
    pool = [
        item
        for item in QUESTIONS.values()
        if item["format"] == fmt and (not lane or lane == "any" or item["lane"] == lane) and item["id"] != exclude
    ]
    if not pool:
        pool = [item for item in QUESTIONS.values() if item["format"] == fmt and item["id"] != exclude]
    if not pool:
        pool = [item for item in QUESTIONS.values() if item["format"] == fmt]
    return dict(random.choice(pool))


def sample_interview() -> dict:
    payload = json.loads((ROOT / "data" / "sample_interview.json").read_text(encoding="utf-8"))
    question = get_question(payload["question_id"])
    return {
        "question": question,
        "answer": payload["answer"],
        "seconds": payload["seconds"],
        "note": "Sample clock is 31 seconds on a 25-second question, so the timing note has something to catch.",
    }
