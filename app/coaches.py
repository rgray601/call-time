"""The staff: interview coach, news desk, runway coach, and the director who releases a note.

Contestants pick a room. That room's coach does the work with tools. The director
reviews interview and news before anything is released. The runway room uses a
Python auditor instead of a second model, because the failure mode there is an
invented measurement and a checker is stricter than another prose pass.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
from dataclasses import dataclass
from typing import Any, AsyncIterator

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import BaseToolCallPart, BaseToolReturnPart, RetryPromptPart
from pydantic_ai.usage import UsageLimits

from app import news, questions
from app.budget import BudgetError, charge, gate
from app.config import MODEL_NAME, ROOT, build_model, model_settings
from app.pricing import add_usage, cost_usd, usage_dict
from app.safety import appearance_problem, hold_message, screen_input, wrap_untrusted
from app.walk import (
    SAMPLE_WALK,
    WalkMetrics,
    audit_note,
    flags_for,
    measured_fallback,
)

PROMPTS = ROOT / "app" / "prompts"
FULL_MANUAL = (PROMPTS / "manual.md").read_text(encoding="utf-8")
SHORT_MANUAL = (PROMPTS / "manual_short.md").read_text(encoding="utf-8")
INTERVIEW_PROMPT = (PROMPTS / "interview.md").read_text(encoding="utf-8")
NEWS_PROMPT = (PROMPTS / "news.md").read_text(encoding="utf-8")
RUNWAY_PROMPT = (PROMPTS / "runway.md").read_text(encoding="utf-8")
DIRECTOR_PROMPT = (PROMPTS / "director.md").read_text(encoding="utf-8")

MANUAL_MODE: contextvars.ContextVar[str] = contextvars.ContextVar("manual_mode", default="full")
_BRIEFS: dict[str, dict] = {}


def _manual() -> str:
    return SHORT_MANUAL if MANUAL_MODE.get() == "short" else FULL_MANUAL


def _clip(value: Any, limit: int = 480) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _public_error(exc: Exception) -> str:
    text = str(exc)
    if "Invalid API Key" in text or "API_KEY is missing" in text or "incorrect API key" in text.lower():
        return (
            "The API key was rejected, so the coach did not run. "
            "Put a current OPENAI_API_KEY in .env in this folder, or in the folder above the repo."
        )
    text = re.sub(r"sk-[A-Za-z0-9_\-]+", "[key]", text)
    text = re.sub(r"Bearer\s+\S+", "Bearer [key]", text, flags=re.I)
    return text[:400]


def _tools(messages: list[Any]) -> list[dict]:
    calls: list[dict] = []
    by_id: dict[str, dict] = {}
    for message in messages:
        for part in getattr(message, "parts", []):
            if isinstance(part, BaseToolCallPart):
                entry = {"name": part.tool_name, "args": _clip(part.args), "result": None}
                calls.append(entry)
                by_id[part.tool_call_id] = entry
            elif isinstance(part, (BaseToolReturnPart, RetryPromptPart)):
                entry = by_id.get(getattr(part, "tool_call_id", ""))
                if entry is not None and entry["result"] is None:
                    content = getattr(part, "content", None)
                    entry["result"] = _clip(content)
    return calls


def _timing(answer: str, seconds: float, limit: int) -> dict:
    words = re.findall(r"[A-Za-z0-9']+", answer)
    estimate = round(len(words) / 138 * 60, 1)
    used_clock = seconds is not None and seconds > 0
    used = round(float(seconds), 1) if used_clock else estimate
    return {
        "words": len(words),
        "seconds_used": used,
        "seconds_limit": limit,
        "over_by": round(used - limit, 1),
        "source": "clock" if used_clock else "estimated at 138 words per minute",
        "estimate_seconds": estimate,
    }


def _fillers(answer: str) -> dict:
    text = answer.lower()
    counts = {
        "um": len(re.findall(r"\bum+\b", text)),
        "uh": len(re.findall(r"\buh+\b", text)),
        "you know": len(re.findall(r"\byou know\b", text)),
    }
    return {"counts": counts, "total": sum(counts.values())}


class InterviewDraft(BaseModel):
    content: int = Field(ge=1, le=10)
    structure: int = Field(ge=1, le=10)
    authenticity: int = Field(ge=1, le=10)
    timing: int = Field(ge=1, le=10)
    overall: int = Field(ge=1, le=10)
    judge_heard: str
    tighter_answer: str
    drill: str
    used_question_id: str


class DirectorRelease(BaseModel):
    release: bool
    judges_note: str
    held_reason: str = ""


class JudgeQuestion(BaseModel):
    headline_id: str
    question: str
    why: str


class NewsBriefDraft(BaseModel):
    brief: str
    questions: list[JudgeQuestion] = Field(min_length=1, max_length=3)


class AnswerGrade(BaseModel):
    headline_id: str
    score: int = Field(ge=1, le=10)
    used_a_fact: bool
    note: str
    tighter_answer: str


class NewsGradeDraft(BaseModel):
    grades: list[AnswerGrade] = Field(min_length=1, max_length=3)


class RunwayDraft(BaseModel):
    overall: int = Field(ge=1, le=10)
    measured: list[str] = Field(min_length=1, max_length=4)
    drill: str
    turn: str


@dataclass
class InterviewDeps:
    question: dict
    answer: str
    seconds: float


@dataclass
class NewsDeps:
    packet: dict
    mode: str
    answers: list[dict]


@dataclass
class RunwayDeps:
    metrics: WalkMetrics
    flags: list[dict]


@dataclass
class DirectorDeps:
    payload: dict


interview_agent = Agent(deps_type=InterviewDeps, output_type=InterviewDraft, name="interview_coach")
runway_agent = Agent(deps_type=RunwayDeps, output_type=RunwayDraft, name="runway_coach")
director_agent = Agent(deps_type=DirectorDeps, output_type=DirectorRelease, name="director")


@interview_agent.instructions
def _interview_instructions(_ctx: RunContext[InterviewDeps]) -> str:
    return _manual() + "\n\n" + INTERVIEW_PROMPT


@runway_agent.instructions
def _runway_instructions(_ctx: RunContext[RunwayDeps]) -> str:
    return FULL_MANUAL + "\n\n" + RUNWAY_PROMPT


@director_agent.instructions
def _director_instructions(_ctx: RunContext[DirectorDeps]) -> str:
    return _manual() + "\n\n" + DIRECTOR_PROMPT


@interview_agent.tool
def get_question(ctx: RunContext[InterviewDeps]) -> dict:
    """Return the question this contestant was asked, including the clock and what to listen for."""
    item = ctx.deps.question
    return {
        "id": item["id"],
        "format": item["format"],
        "lane": item["lane"],
        "seconds": item["seconds"],
        "text": item["text"],
        "listen_for": item["listen_for"],
    }


@interview_agent.tool
def timing_check(ctx: RunContext[InterviewDeps]) -> dict:
    """Compare the answer to the rehearsal clock. Uses the clock if she ran it, otherwise word count."""
    return _timing(ctx.deps.answer, ctx.deps.seconds, int(ctx.deps.question["seconds"]))


@interview_agent.tool
def filler_count(ctx: RunContext[InterviewDeps]) -> dict:
    """Count um, uh, and you know. Does not count the ordinary word like."""
    return _fillers(ctx.deps.answer)


@runway_agent.tool
def read_metrics(ctx: RunContext[RunwayDeps]) -> dict:
    """Return the measured walk and the flags. This is the only evidence."""
    return {"metrics": ctx.deps.metrics.model_dump(), "flags": ctx.deps.flags}


@director_agent.tool
def grounding_check(ctx: RunContext[DirectorDeps]) -> dict:
    """Return the coach draft and the Python audit. Call this before you release or hold."""
    return ctx.deps.payload


def _agent_event(name: str, meta: dict) -> dict:
    return {
        "type": "agent",
        "name": name,
        "tools": meta["tools"],
        "usage": meta["usage"],
        "usd": meta["usd"],
        "model": MODEL_NAME,
    }


async def _execute(agent: Agent, prompt: str, deps: Any, session_id: str, *, count_run: bool = False):
    gate(session_id)
    result = await asyncio.wait_for(
        agent.run(
            prompt,
            deps=deps,
            model=build_model(),
            model_settings=model_settings(),
            usage_limits=UsageLimits(request_limit=5, tool_calls_limit=8),
        ),
        timeout=75,
    )
    raw_usage = result.usage
    usage = usage_dict(raw_usage() if callable(raw_usage) and not hasattr(raw_usage, "input_tokens") else raw_usage)
    usd = cost_usd(usage)
    spend = charge(session_id, usd, is_run=count_run)
    return result.output, {
        "usage": usage,
        "usd": round(usd, 6),
        "tools": _tools(result.all_messages()),
        "spend": spend,
    }


def _receipt(rows: list[dict], spend: dict | None) -> dict:
    return {
        "model": MODEL_NAME,
        "agents": [
            {
                "name": row["name"],
                "usage": row["usage"],
                "usd": row["usd"],
                "tools": [tool["name"] for tool in row["tools"]],
            }
            for row in rows
        ],
        "usage": add_usage([row["usage"] for row in rows]) if rows else add_usage([]),
        "usd": round(sum(row["usd"] for row in rows), 6),
        "spend": spend,
    }


def _blocked(kind: str, message: str, spend: dict | None = None) -> dict:
    return {
        "type": "result",
        "ok": True,
        "blocked": kind,
        "message": message,
        "receipt": _receipt([], spend),
        "contestant": None,
        "backstage": {"agents": [], "held": kind == "body"},
    }


def _audit_interview(draft: InterviewDraft, question: dict, timing: dict) -> dict:
    blob = " ".join([draft.judge_heard, draft.tighter_answer, draft.drill])
    problems = []
    if appearance_problem(blob):
        problems.append("appearance")
    if draft.used_question_id != question["id"]:
        problems.append("question id does not match the question she was asked")
    cap = {"onstage": 60, "final": 75, "private": 160}.get(question["format"], 60)
    words = len(draft.tighter_answer.split())
    return {
        "problems": problems,
        "hold": bool(problems),
        "tighter_words": words,
        "tighter_cap": cap,
        "tighter_over_cap": words > cap,
        "timing": timing,
        "tools_note": "Scores belong to the interview coach. Do not rescore.",
    }


def _apply_director(draft_fields: list[str], release: DirectorRelease | None, meta_ok: bool) -> dict:
    draft_blob = " ".join(draft_fields)
    if appearance_problem(draft_blob):
        return {"reviewed": True, "released": False, "note": hold_message(), "held_reason": "appearance"}
    if release is None or not meta_ok:
        cleaned = draft_fields[0] if draft_fields else ""
        if appearance_problem(cleaned):
            return {"reviewed": False, "released": False, "note": hold_message(), "held_reason": "appearance"}
        return {
            "reviewed": False,
            "released": True,
            "note": cleaned,
            "held_reason": "",
        }
    if not release.release or appearance_problem(release.judges_note):
        reason = release.held_reason or "The director held the draft."
        if appearance_problem(release.judges_note) or appearance_problem(reason):
            reason = hold_message()
        return {"reviewed": True, "released": False, "note": reason, "held_reason": reason}
    return {"reviewed": True, "released": True, "note": release.judges_note.strip(), "held_reason": ""}


async def iter_interview(
    session_id: str,
    question_id: str,
    answer: str,
    seconds: float,
    *,
    review: bool = True,
    manual: str = "full",
) -> AsyncIterator[dict]:
    token = MANUAL_MODE.set(manual if manual in ("full", "short") else "full")
    try:
        question = questions.get_question(question_id)
        if question is None:
            yield {"type": "error", "message": "That question is not in the book."}
            return
        screened = screen_input(answer)
        if not screened["allow"]:
            yield _blocked(screened["kind"], screened["message"])
            return
        try:
            gate(session_id)
        except BudgetError as exc:
            yield _blocked("budget", exc.message, exc.snapshot)
            return

        yield {"type": "status", "text": "The interview coach is at the table."}
        deps = InterviewDeps(question=question, answer=screened["text"], seconds=float(seconds or 0))
        prompt = (
            "Score this rehearsal. Call get_question, timing_check, and filler_count before you score.\n\n"
            + wrap_untrusted("contestant_answer", screened["text"])
        )
        rows: list[dict] = []
        try:
            draft, meta = await _execute(interview_agent, prompt, deps, session_id, count_run=True)
        except BudgetError as exc:
            yield _blocked("budget", exc.message, exc.snapshot)
            return
        except (asyncio.TimeoutError, Exception) as exc:
            yield {"type": "error", "message": "The interview coach did not finish. " + _public_error(exc)}
            return
        rows.append({"name": "Interview Coach", **meta})
        yield _agent_event("Interview Coach", meta)

        timing = _timing(screened["text"], float(seconds or 0), int(question["seconds"]))
        audit = _audit_interview(draft, question, timing)
        release = None
        if review:
            yield {"type": "status", "text": "The director is reading the note."}
            director_deps = DirectorDeps(
                payload={
                    "room": "interview",
                    "question": question,
                    "draft": draft.model_dump(),
                    "audit": audit,
                    "injection_stripped": screened["stripped"],
                }
            )
            director_prompt = (
                "Review the interview draft. Call grounding_check before you release or hold. "
                "The contestant does not see the draft. She sees only your judges_note."
            )
            try:
                release, director_meta = await _execute(director_agent, director_prompt, director_deps, session_id)
            except BudgetError as exc:
                release = None
                director_meta = None
                yield {"type": "status", "text": "The director stopped because the budget lock tripped."}
            except (asyncio.TimeoutError, Exception) as exc:
                release = None
                director_meta = None
                yield {"type": "status", "text": "The director did not finish. The coach's note is marked unreviewed."}
                _ = exc
            if director_meta:
                rows.append({"name": "Director", **director_meta})
                yield _agent_event("Director", director_meta)

        decision = _apply_director(
            [draft.judge_heard, draft.tighter_answer, draft.drill],
            release,
            meta_ok=True,
        )
        show_craft = decision["released"]
        yield {
            "type": "result",
            "ok": True,
            "blocked": "injection_stripped" if screened["stripped"] else None,
            "message": screened["message"],
            "receipt": _receipt(rows, rows[-1]["spend"] if rows else None),
            "contestant": {
                "question": question,
                "scores": {
                    "content": draft.content,
                    "structure": draft.structure,
                    "authenticity": draft.authenticity,
                    "timing": draft.timing,
                    "overall": draft.overall,
                },
                "judges_note": decision["note"],
                "tighter_answer": draft.tighter_answer if show_craft else "",
                "drill": draft.drill if show_craft else "",
                "clock": timing,
                "reviewed": decision["reviewed"],
                "released": decision["released"],
                "fillers": _fillers(screened["text"]),
            },
            "backstage": {
                "agents": [
                    {"name": row["name"], "tools": row["tools"], "usage": row["usage"], "usd": row["usd"]}
                    for row in rows
                ],
                "coach_draft": draft.model_dump() if decision["released"] else {"held": True},
                "audit": audit,
                "director": release.model_dump() if release else None,
                "manual": MANUAL_MODE.get(),
                "reviewed": decision["reviewed"],
            },
        }
    finally:
        MANUAL_MODE.reset(token)


async def iter_news_brief(session_id: str) -> AsyncIterator[dict]:
    packet = news.get_headlines()
    if not packet.get("headlines"):
        yield _blocked("news", packet.get("error") or "No headlines to rehearse.")
        return
    try:
        gate(session_id)
    except BudgetError as exc:
        yield _blocked("budget", exc.message, exc.snapshot)
        return
    yield {"type": "status", "text": "The news desk is reading the wires."}
    deps = NewsDeps(packet=packet, mode="brief", answers=[])
    prompt = (
        "Write tonight's brief and exactly three judge questions. "
        "Call get_headlines, then lookup_headline for every id you cite."
    )
    try:
        draft, meta = await _run_news(deps, prompt, session_id, NewsBriefDraft, count_run=True)
    except BudgetError as exc:
        yield _blocked("budget", exc.message, exc.snapshot)
        return
    except (asyncio.TimeoutError, Exception) as exc:
        yield {"type": "error", "message": "The news desk did not finish. " + _public_error(exc)}
        return
    rows = [{"name": "News Desk", **meta}]
    yield _agent_event("News Desk", meta)

    known = {item["id"] for item in packet["headlines"]}
    questions_out = [item for item in draft.questions if item.headline_id in known]
    unknown = [item.headline_id for item in draft.questions if item.headline_id not in known]
    problems = []
    if unknown:
        problems.append("cited a headline that was not in the packet: " + ", ".join(unknown))
    if packet.get("practice_set") and "practice" not in draft.brief.lower():
        problems.append("practice set was not labeled")
    if appearance_problem(draft.brief):
        problems.append("appearance")
    audit = {"problems": problems, "hold": "appearance" in problems or bool(unknown), "unknown_ids": unknown}

    yield {"type": "status", "text": "The director is checking the headlines against the note."}
    release, director_meta = await _director_or_none(
        session_id,
        {
            "room": "news_brief",
            "draft": draft.model_dump(),
            "audit": audit,
            "packet_ids": list(known),
            "practice_set": packet.get("practice_set"),
            "fetched_at": packet.get("fetched_at"),
        },
    )
    if director_meta:
        rows.append({"name": "Director", **director_meta})
        yield _agent_event("Director", director_meta)
    decision = _apply_director([draft.brief], release, True)
    if unknown and decision["released"]:
        decision = {
            **decision,
            "note": decision["note"]
            + " One question was dropped because its headline was not in the packet the desk actually fetched.",
        }
    if decision["released"] and questions_out:
        _BRIEFS[session_id] = {
            "packet": packet,
            "brief": draft.brief,
            "questions": [item.model_dump() for item in questions_out],
        }
    yield {
        "type": "result",
        "ok": True,
        "blocked": None,
        "message": "",
        "receipt": _receipt(rows, rows[-1]["spend"]),
        "contestant": {
            "practice_set": packet.get("practice_set"),
            "fetched_at": packet.get("fetched_at"),
            "headlines": packet["headlines"],
            "brief": decision["note"] if decision["released"] else decision["note"],
            "questions": [item.model_dump() for item in questions_out] if decision["released"] else [],
            "reviewed": decision["reviewed"],
            "released": decision["released"],
        },
        "backstage": {
            "agents": [
                {"name": row["name"], "tools": row["tools"], "usage": row["usage"], "usd": row["usd"]} for row in rows
            ],
            "audit": audit,
            "director": release.model_dump() if release else None,
        },
    }


async def iter_news_grade(session_id: str, answers: list[dict]) -> AsyncIterator[dict]:
    stored = _BRIEFS.get(session_id)
    if not stored:
        yield {"type": "error", "message": "Write the judge questions first. Grading needs that packet, not a new one."}
        return
    packet = stored["packet"]
    by_id = {item["headline_id"]: item for item in stored["questions"]}
    prepared = []
    blocked_notes = []
    for answer in answers:
        headline_id = str(answer.get("headline_id") or "")
        question = by_id.get(headline_id)
        if question is None:
            continue
        screened = screen_input(str(answer.get("answer") or ""))
        if not screened["allow"]:
            blocked_notes.append({"headline_id": headline_id, "kind": screened["kind"], "message": screened["message"]})
            continue
        prepared.append(
            {
                "headline_id": headline_id,
                "question": question["question"],
                "answer": screened["text"],
                "stripped": screened["stripped"],
            }
        )
    if not prepared:
        message = blocked_notes[0]["message"] if blocked_notes else "Write an answer first."
        kind = blocked_notes[0]["kind"] if blocked_notes else "empty"
        yield _blocked(kind, message)
        return
    try:
        gate(session_id)
    except BudgetError as exc:
        yield _blocked("budget", exc.message, exc.snapshot)
        return
    yield {"type": "status", "text": "The news desk is grading against the headlines."}
    deps = NewsDeps(packet=packet, mode="grade", answers=prepared)
    blocks = "\n\n".join(
        f"headline_id: {item['headline_id']}\nquestion: {item['question']}\n"
        + wrap_untrusted("contestant_answer", item["answer"])
        for item in prepared
    )
    prompt = "Grade these answers. Call get_headlines and lookup_headline for every id.\n\n" + blocks
    try:
        draft, meta = await _run_news(deps, prompt, session_id, NewsGradeDraft, count_run=True)
    except BudgetError as exc:
        yield _blocked("budget", exc.message, exc.snapshot)
        return
    except (asyncio.TimeoutError, Exception) as exc:
        yield {"type": "error", "message": "The news desk did not finish. " + _public_error(exc)}
        return
    rows = [{"name": "News Desk", **meta}]
    yield _agent_event("News Desk", meta)
    known = {item["id"] for item in packet["headlines"]}
    grades = [item for item in draft.grades if item.headline_id in known and item.headline_id in by_id]
    problems = []
    if len(grades) != len(draft.grades):
        problems.append("dropped a grade that cited an unknown headline")
    blob = " ".join(item.note + " " + item.tighter_answer for item in grades)
    if appearance_problem(blob):
        problems.append("appearance")
    audit = {"problems": problems, "hold": "appearance" in problems}
    yield {"type": "status", "text": "The director is checking the grades."}
    release, director_meta = await _director_or_none(
        session_id,
        {"room": "news_grade", "draft": draft.model_dump(), "audit": audit, "packet_ids": list(known)},
    )
    if director_meta:
        rows.append({"name": "Director", **director_meta})
        yield _agent_event("Director", director_meta)
    decision = _apply_director([blob], release, True)
    yield {
        "type": "result",
        "ok": True,
        "blocked": None,
        "message": "",
        "receipt": _receipt(rows, rows[-1]["spend"]),
        "contestant": {
            "reviewed": decision["reviewed"],
            "released": decision["released"],
            "judges_note": decision["note"] if decision["released"] else decision["note"],
            "grades": [item.model_dump() for item in grades] if decision["released"] else [],
            "blocked_answers": blocked_notes,
            "practice_set": packet.get("practice_set"),
        },
        "backstage": {
            "agents": [
                {"name": row["name"], "tools": row["tools"], "usage": row["usage"], "usd": row["usd"]} for row in rows
            ],
            "audit": audit,
            "director": release.model_dump() if release else None,
        },
    }


async def _run_news(deps: NewsDeps, prompt: str, session_id: str, output_type: type[BaseModel], *, count_run: bool = False):
    """News desk returns either a brief or a grade card. The agent is rebuilt per mode.

    A single Agent cannot switch output_type after construction, so this clones
    the registered tools onto a typed agent for the run.
    """
    gate(session_id)
    typed = Agent(deps_type=NewsDeps, output_type=output_type, name="news_desk")

    @typed.instructions
    def _instr(ctx: RunContext[NewsDeps]) -> str:
        return _manual() + "\n\n" + NEWS_PROMPT + "\n\nThis run is: " + ctx.deps.mode + "."

    @typed.tool
    def get_headlines(ctx: RunContext[NewsDeps]) -> dict:
        """Return the only headlines this rehearsal may use."""
        return ctx.deps.packet

    @typed.tool
    def lookup_headline(ctx: RunContext[NewsDeps], headline_id: str) -> dict:
        """Look up one headline by id. Unknown ids return not_found."""
        for item in ctx.deps.packet.get("headlines") or []:
            if item["id"] == headline_id:
                return item
        return {"not_found": headline_id}

    result = await asyncio.wait_for(
        typed.run(
            prompt,
            deps=deps,
            model=build_model(),
            model_settings=model_settings(),
            usage_limits=UsageLimits(request_limit=5, tool_calls_limit=8),
        ),
        timeout=75,
    )
    raw_usage = result.usage
    usage = usage_dict(raw_usage() if callable(raw_usage) and not hasattr(raw_usage, "input_tokens") else raw_usage)
    usd = cost_usd(usage)
    spend = charge(session_id, usd, is_run=count_run)
    return result.output, {
        "usage": usage,
        "usd": round(usd, 6),
        "tools": _tools(result.all_messages()),
        "spend": spend,
    }


async def _director_or_none(session_id: str, payload: dict) -> tuple[DirectorRelease | None, dict | None]:
    try:
        release, meta = await _execute(
            director_agent,
            "Call grounding_check. Then release a note or hold it. The contestant sees only judges_note.",
            DirectorDeps(payload=payload),
            session_id,
        )
        return release, meta
    except BudgetError:
        return None, None
    except (asyncio.TimeoutError, Exception):
        return None, None


async def iter_runway(session_id: str, metrics: WalkMetrics) -> AsyncIterator[dict]:
    flags = flags_for(metrics)
    if metrics.pose_detected_pct < 0.4 and not metrics.sample:
        yield _blocked(
            "framing",
            "The camera lost the walk, so the coach was not called. Step back until your shoulders and hips stay in frame, then record again.",
        )
        return
    try:
        gate(session_id)
    except BudgetError as exc:
        yield _blocked("budget", exc.message, exc.snapshot)
        return
    yield {"type": "status", "text": "The runway coach is reading the measurements."}
    deps = RunwayDeps(metrics=metrics, flags=flags)
    prompt = "Coach this take. Call read_metrics first. Address every flag and no other problem."
    try:
        draft, meta = await _execute(runway_agent, prompt, deps, session_id, count_run=True)
    except BudgetError as exc:
        yield _blocked("budget", exc.message, exc.snapshot)
        return
    except (asyncio.TimeoutError, Exception) as exc:
        yield {"type": "error", "message": "The runway coach did not finish. " + _public_error(exc)}
        return
    note = " ".join([*draft.measured, draft.drill, draft.turn])
    audit = audit_note(note, metrics)
    if audit["ok"] and appearance_problem(note):
        audit = {"ok": False, "problem": "appearance"}
    released = note if audit["ok"] else measured_fallback(metrics)
    yield _agent_event("Runway Coach", meta)
    yield {
        "type": "result",
        "ok": True,
        "blocked": None if audit["ok"] else "auditor",
        "message": ""
        if audit["ok"]
        else "The runway auditor replaced the coach's note. It went outside the measurements: " + audit["problem"],
        "receipt": _receipt([{"name": "Runway Coach", **meta}], meta["spend"]),
        "contestant": {
            "overall": draft.overall if audit["ok"] else None,
            "note": released,
            "drill": draft.drill if audit["ok"] else "",
            "turn": draft.turn if audit["ok"] else "",
            "bullets": draft.measured if audit["ok"] else [],
            "flags": flags,
            "metrics": metrics.model_dump(),
            "auditor_passed": audit["ok"],
            "sample": metrics.sample,
        },
        "backstage": {
            "agents": [
                {
                    "name": "Runway Coach",
                    "tools": meta["tools"],
                    "usage": meta["usage"],
                    "usd": meta["usd"],
                }
            ],
            "audit": audit,
            "flags": flags,
        },
    }


def walk_audit_demo() -> dict:
    bad = (
        "You looked nervous and your smile dropped on the turn. "
        "You should lose weight before gown. Your shoulders were at 40 degrees."
    )
    audit = audit_note(bad, SAMPLE_WALK)
    return {
        "input": bad,
        "audit": audit,
        "released": measured_fallback(SAMPLE_WALK),
        "sample_flags": flags_for(SAMPLE_WALK),
    }
