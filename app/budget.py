"""Session and daily spend caps.

The file lives in output/spend.json. It stores dollars and timestamps, never answers.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from app.config import DAILY_USD, HOUR_RUNS, ROOT, SESSION_USD

PATH = ROOT / "output" / "spend.json"
_LOCK = threading.Lock()


class BudgetError(Exception):
    def __init__(self, message: str, snapshot: dict):
        super().__init__(message)
        self.message = message
        self.snapshot = snapshot


def _empty() -> dict:
    return {"sessions": {}}


def _load() -> dict:
    if not PATH.exists():
        return _empty()
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty()
    data.setdefault("sessions", {})
    return data


def _save(data: dict) -> None:
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(data), encoding="utf-8")


def _day_key(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _prune(events: list[dict], now: float) -> list[dict]:
    cutoff = now - 2 * 24 * 3600
    return [event for event in events if float(event.get("t") or 0) >= cutoff]


def snapshot(session_id: str, *, session_limit: float = SESSION_USD, data: dict | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    data = _load() if data is None else data
    day = _day_key(now)
    daily = 0.0
    for session in data["sessions"].values():
        for event in session.get("events", []):
            if time.strftime("%Y-%m-%d", time.gmtime(float(event.get("t") or 0))) == day:
                daily += float(event.get("usd") or 0)
    events = _prune(data["sessions"].get(session_id, {}).get("events", []), now)
    hour_ago = now - 3600
    hour_runs = sum(1 for event in events if float(event["t"]) >= hour_ago and event.get("run"))
    session_usd = sum(float(event.get("usd") or 0) for event in events)
    return {
        "session_usd": round(session_usd, 6),
        "session_limit": session_limit,
        "daily_usd": round(daily, 6),
        "daily_limit": DAILY_USD,
        "hour_runs": hour_runs,
        "hour_limit": HOUR_RUNS,
        "model": "gpt-6-luna",
    }


def over_limit(snap: dict) -> str | None:
    if snap["session_usd"] >= snap["session_limit"] - 1e-9:
        return (
            "This session has used its $"
            f"{snap['session_limit']:.2f} rehearsal budget, so the coaches have stopped. "
            "The cap is what keeps a bug, or a pasted loop, from spending the key."
        )
    if snap["daily_usd"] >= snap["daily_limit"] - 1e-9:
        return (
            f"Call Time has used today's ${snap['daily_limit']:.2f} shared budget. "
            "Coaching stops until the next UTC day."
        )
    if snap["hour_runs"] >= snap["hour_limit"]:
        return (
            f"This session has started {snap['hour_limit']} rehearsals in an hour. "
            "That cap is there to stop a runaway loop."
        )
    return None


def gate(session_id: str) -> dict:
    with _LOCK:
        snap = snapshot(session_id)
        reason = over_limit(snap)
        if reason:
            raise BudgetError(reason, snap)
        return snap


def charge(session_id: str, usd: float, *, is_run: bool = False) -> dict:
    now = time.time()
    with _LOCK:
        data = _load()
        session = data["sessions"].setdefault(session_id, {"events": []})
        session["events"] = _prune(session.get("events", []), now)
        session["events"].append({"t": now, "usd": float(usd), "run": bool(is_run)})
        _save(data)
        return snapshot(session_id, data=data, now=now)


def demo_block() -> dict:
    """Same over_limit check the live gate uses, pointed at an empty wallet."""
    snap = {
        "session_usd": SESSION_USD,
        "session_limit": SESSION_USD,
        "daily_usd": 0.0,
        "daily_limit": DAILY_USD,
        "hour_runs": 0,
        "hour_limit": HOUR_RUNS,
        "model": "gpt-6-luna",
    }
    reason = over_limit(snap)
    if reason is None:
        raise RuntimeError("Budget demo failed to trip the lock.")
    return {"blocked": True, "kind": "budget", "message": reason, "spend": snap, "usd": 0.0}
