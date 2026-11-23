"""Walk measurements and the flags a coach is allowed to talk about.

The browser computes the summary. This module decides what the numbers mean,
so the model cannot invent a problem the camera did not measure.

Definitions, matched in static/app.js:
- shoulder tilt: degrees off horizontal between the two shoulders
- head_drop_pct: share of good frames whose nose-to-shoulder gap fell under
  75% of that take's own median gap
- cadence: alternating ankle steps per second
- center_drift: standard deviation of the hip midpoint, in normalized x
- arm_range: standard deviation of each wrist's horizontal distance from its
  shoulder, divided by shoulder width, averaged left and right
- turn: shoulder width fell under 72% of the median width for several frames
- turn_shoulder_dip_deg: the worst shoulder tilt during those turn frames
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

WalkStyle = Literal["evening_gown", "opening_number"]

TARGETS = {
    "evening_gown": {"cadence": 1.0, "arm_high": 0.12, "label": "Evening gown"},
    "opening_number": {"cadence": 1.6, "arm_high": 0.20, "label": "Opening number"},
}


class WalkMetrics(BaseModel):
    walk_style: WalkStyle
    duration_sec: float = Field(ge=0, le=60)
    pose_detected_pct: float = Field(ge=0, le=1)
    shoulder_tilt_deg_mean: float = Field(ge=0, le=90)
    shoulder_tilt_deg_worst: float = Field(ge=0, le=90)
    head_drop_pct: float = Field(ge=0, le=1)
    cadence_steps_per_sec: float = Field(ge=0, le=5)
    sway_beats: int = Field(ge=0, le=80)
    turn_detected: bool
    turn_shoulder_dip_deg: float = Field(ge=0, le=90)
    center_drift: float = Field(ge=0, le=1)
    arm_range: float = Field(ge=0, le=2)
    frames: int = Field(ge=0, le=3000)
    sample: bool = False


SAMPLE_WALK = WalkMetrics(
    walk_style="evening_gown",
    duration_sec=12.0,
    pose_detected_pct=0.96,
    shoulder_tilt_deg_mean=3.1,
    shoulder_tilt_deg_worst=4.4,
    head_drop_pct=0.22,
    cadence_steps_per_sec=0.95,
    sway_beats=7,
    turn_detected=True,
    turn_shoulder_dip_deg=9.1,
    center_drift=0.06,
    arm_range=0.04,
    frames=144,
    sample=True,
)


def flags_for(metrics: WalkMetrics) -> list[dict]:
    """Problems worth a note. Empty means the take is inside the rehearsal targets."""
    target = TARGETS[metrics.walk_style]
    flags: list[dict] = []
    if metrics.pose_detected_pct < 0.55:
        flags.append(
            {
                "code": "framing",
                "detail": (
                    f"The camera only held the pose on {metrics.pose_detected_pct:.0%} of frames. "
                    "Step back until both shoulders and both hips stay in frame."
                ),
            }
        )
    if metrics.shoulder_tilt_deg_mean > 5:
        flags.append(
            {
                "code": "shoulders",
                "detail": (
                    f"Average shoulder tilt was {metrics.shoulder_tilt_deg_mean:.1f} degrees. "
                    "The rehearsal target is under 5 degrees off horizontal."
                ),
            }
        )
    if metrics.head_drop_pct > 0.15:
        flags.append(
            {
                "code": "head",
                "detail": (
                    f"The head dropped on {metrics.head_drop_pct:.0%} of frames, "
                    "against this take's own starting line. The target is under 15%."
                ),
            }
        )
    cadence_gap = metrics.cadence_steps_per_sec - target["cadence"]
    if abs(cadence_gap) > 0.25:
        direction = "fast" if cadence_gap > 0 else "slow"
        flags.append(
            {
                "code": "cadence",
                "detail": (
                    f"Cadence was {metrics.cadence_steps_per_sec:.2f} steps per second, "
                    f"which is {direction} for {target['label'].lower()} "
                    f"(target {target['cadence']:.1f})."
                ),
            }
        )
    if not metrics.turn_detected:
        flags.append(
            {
                "code": "turn",
                "detail": "No turn showed up in the take. A pageant pass needs a pivot at the end.",
            }
        )
    elif metrics.turn_shoulder_dip_deg > 8:
        flags.append(
            {
                "code": "turn_dip",
                "detail": (
                    f"On the turn, the shoulders dipped to {metrics.turn_shoulder_dip_deg:.1f} degrees. "
                    "The target is to stay under 8 degrees through the pivot."
                ),
            }
        )
    if metrics.center_drift > 0.12:
        flags.append(
            {
                "code": "drift",
                "detail": (
                    f"Center drift was {metrics.center_drift:.2f} (normalized). "
                    "The target is under 0.12, which reads as one lane, not a wander."
                ),
            }
        )
    if metrics.arm_range < 0.015:
        flags.append(
            {
                "code": "arms_locked",
                "detail": (
                    f"Arm range was {metrics.arm_range:.3f}, almost no movement. "
                    "Quiet arms are right for a gown, locked arms are not."
                ),
            }
        )
    elif metrics.arm_range > target["arm_high"]:
        flags.append(
            {
                "code": "arms_swing",
                "detail": (
                    f"Arm range was {metrics.arm_range:.2f}, above the {target['label'].lower()} "
                    f"target of {target['arm_high']:.2f}."
                ),
            }
        )
    return flags


def numbers_in(metrics: WalkMetrics) -> set[str]:
    """Rounded forms the coach is allowed to quote."""
    values = [
        metrics.duration_sec,
        metrics.pose_detected_pct,
        metrics.pose_detected_pct * 100,
        metrics.shoulder_tilt_deg_mean,
        metrics.shoulder_tilt_deg_worst,
        metrics.head_drop_pct,
        metrics.head_drop_pct * 100,
        metrics.cadence_steps_per_sec,
        metrics.sway_beats,
        metrics.turn_shoulder_dip_deg,
        metrics.center_drift,
        metrics.arm_range,
        metrics.frames,
        TARGETS[metrics.walk_style]["cadence"],
        TARGETS[metrics.walk_style]["arm_high"],
        5,
        8,
        15,
        0.12,
        0.55,
        12,
        *range(0, 11),
    ]
    allowed: set[str] = set()
    for value in values:
        allowed.add(str(int(round(value))))
        allowed.add(f"{value:.0f}")
        allowed.add(f"{value:.1f}")
        allowed.add(f"{value:.2f}")
        allowed.add(f"{value:.3f}")
    return allowed


def audit_note(text: str, metrics: WalkMetrics) -> dict:
    """Reject a walk note that invents a feeling or quotes a number we did not measure."""
    import re

    problem = unmeasured_problem(text)
    if problem:
        return {"ok": False, "problem": f"unmeasured claim: {problem}"}
    allowed = numbers_in(metrics)
    for raw in re.findall(r"\d+(?:\.\d+)?", text or ""):
        if raw in allowed:
            continue
        try:
            number = float(raw)
        except ValueError:
            return {"ok": False, "problem": f"unexplained number {raw}"}
        if any(raw == form or abs(number - float(form)) < 0.051 for form in allowed):
            continue
        # Percents written without being in the packet still have to match a packet percent.
        return {"ok": False, "problem": f"unexplained number {raw}"}
    return {"ok": True, "problem": ""}


def unmeasured_problem(text: str) -> str | None:
    from app.safety import appearance_problem, unmeasured_walk_problem

    return appearance_problem(text) or unmeasured_walk_problem(text)


def measured_fallback(metrics: WalkMetrics) -> str:
    """A note built only from flags, used when the coach invents something."""
    flags = flags_for(metrics)
    style = TARGETS[metrics.walk_style]["label"]
    if not flags:
        return (
            f"{style} take, {metrics.duration_sec:.0f} seconds. "
            f"Shoulders averaged {metrics.shoulder_tilt_deg_mean:.1f} degrees off level, "
            f"cadence was {metrics.cadence_steps_per_sec:.2f} steps per second, "
            f"and the head stayed up on {1 - metrics.head_drop_pct:.0%} of frames. "
            "Nothing left the rehearsal targets. Drill the turn once more, and keep the shoulders where they are."
        )
    lines = " ".join(flag["detail"] for flag in flags)
    return (
        f"{style} take. The camera measured this, and only this. {lines} "
        "Drill the flagged piece once, on the beat, and record again."
    )
