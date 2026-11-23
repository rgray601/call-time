"""Call Time web app. One process, static page, coaching routes."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.budget import demo_block, snapshot
from app.config import DAILY_USD, MODEL_NAME, ROOT, SESSION_USD, key_is_set
from app.coaches import (
    iter_interview,
    iter_news_brief,
    iter_news_grade,
    iter_runway,
    walk_audit_demo,
)
from app.news import get_headlines
from app.questions import FORMATS, LANES, draw_question, sample_interview
from app.walk import SAMPLE_WALK, WalkMetrics, flags_for

STATIC = ROOT / "static"
app = FastAPI(title="Call Time")


class InterviewIn(BaseModel):
    question_id: str = Field(max_length=80)
    answer: str = Field(max_length=1500)
    seconds: float = 0


class GradeAnswer(BaseModel):
    headline_id: str = Field(max_length=20)
    answer: str = Field(max_length=800)


class GradeIn(BaseModel):
    answers: list[GradeAnswer] = Field(min_length=1, max_length=3)


def _session(session_id: str | None) -> str:
    sid = (session_id or "anon").strip()[:80]
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,80}", sid):
        return "anon"
    return sid


def _ndjson(source: AsyncIterator[dict]) -> StreamingResponse:
    async def generate():
        try:
            async for event in source:
                yield json.dumps(event, default=str) + "\n"
        except Exception as exc:
            text = str(exc)
            text = re.sub(r"sk-[A-Za-z0-9_\-]+", "[key]", text)[:400]
            yield json.dumps({"type": "error", "message": text}) + "\n"

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": MODEL_NAME,
        "key_set": key_is_set(),
        "session_limit": SESSION_USD,
        "daily_limit": DAILY_USD,
    }


@app.get("/api/meta")
def meta():
    return {"formats": FORMATS, "lanes": LANES, "model": MODEL_NAME}


@app.get("/api/session")
def session(x_session_id: str | None = Header(default=None)):
    return snapshot(_session(x_session_id))


@app.get("/api/question")
def question(fmt: str = "onstage", lane: str = "", exclude: str = ""):
    if fmt not in FORMATS:
        raise HTTPException(status_code=400, detail="Unknown format.")
    return draw_question(fmt, lane or None, exclude or None)


@app.get("/api/sample/interview")
def sample_interview_route():
    return sample_interview()


@app.get("/api/sample/walk")
def sample_walk_route():
    return SAMPLE_WALK.model_dump()


@app.get("/api/headlines")
def headlines():
    return get_headlines()


@app.post("/api/interview")
def interview(body: InterviewIn, x_session_id: str | None = Header(default=None)):
    return _ndjson(iter_interview(_session(x_session_id), body.question_id, body.answer, body.seconds))


@app.post("/api/news/brief")
def news_brief(x_session_id: str | None = Header(default=None)):
    return _ndjson(iter_news_brief(_session(x_session_id)))


@app.post("/api/news/grade")
def news_grade(body: GradeIn, x_session_id: str | None = Header(default=None)):
    payload = [item.model_dump() for item in body.answers]
    return _ndjson(iter_news_grade(_session(x_session_id), payload))


@app.post("/api/runway/preview")
def runway_preview(body: WalkMetrics):
    return {"metrics": body.model_dump(), "flags": flags_for(body)}


@app.post("/api/runway")
def runway(body: WalkMetrics, x_session_id: str | None = Header(default=None)):
    return _ndjson(iter_runway(_session(x_session_id), body))


@app.post("/api/safety/budget")
def safety_budget():
    return demo_block()


@app.post("/api/safety/walk")
def safety_walk():
    return walk_audit_demo()


@app.get("/api/measurements")
def measurements():
    path = ROOT / "output" / "measurements.json"
    if not path.exists():
        return {"ready": False}
    return {"ready": True, **json.loads(path.read_text(encoding="utf-8"))}


@app.get("/")
def home():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
