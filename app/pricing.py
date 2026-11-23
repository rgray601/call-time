"""Dollar cost of gpt-6-luna.

Course prices from Lecture 11, standard tier, per 1M tokens:
  input $0.10, output $0.50
  cache reads 10% of input, cache writes 1.25x input
A request over 272K input tokens is 2x on input and cache, 1.5x on output.
"""

from __future__ import annotations

from app.config import MODEL_NAME

INPUT_PER_MTOK = 0.10
OUTPUT_PER_MTOK = 0.50
CACHE_READ_PER_MTOK = 0.01
CACHE_WRITE_PER_MTOK = 0.125
LONG_CONTEXT = 272_000


def cost_usd(usage: dict) -> float:
    """Cost of one model response. input_tokens already includes cache tokens."""
    inp = int(usage.get("input_tokens") or 0)
    out = int(usage.get("output_tokens") or 0)
    cached_read = int(usage.get("cache_read_tokens") or 0)
    cached_write = int(usage.get("cache_write_tokens") or 0)
    fresh = max(0, inp - cached_read - cached_write)
    long = inp > LONG_CONTEXT
    in_mult = 2.0 if long else 1.0
    out_mult = 1.5 if long else 1.0
    return (
        fresh * INPUT_PER_MTOK * in_mult
        + cached_read * CACHE_READ_PER_MTOK * in_mult
        + cached_write * CACHE_WRITE_PER_MTOK * in_mult
        + out * OUTPUT_PER_MTOK * out_mult
    ) / 1_000_000


def usage_dict(usage) -> dict:
    def n(name: str) -> int:
        return int(getattr(usage, name, 0) or 0)

    details = getattr(usage, "details", None) or {}
    if not isinstance(details, dict):
        try:
            details = dict(details)
        except (TypeError, ValueError):
            details = {}

    cache_read = n("cache_read_tokens") or int(
        details.get("cached_tokens") or details.get("cache_read_tokens") or 0
    )
    cache_write = n("cache_write_tokens") or int(details.get("cache_write_tokens") or 0)
    return {
        "requests": n("requests") or 1,
        "input_tokens": n("input_tokens"),
        "output_tokens": n("output_tokens"),
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "model": MODEL_NAME,
    }


def empty_usage() -> dict:
    return {
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "model": MODEL_NAME,
    }


def add_usage(parts: list[dict]) -> dict:
    total = empty_usage()
    for part in parts:
        for key in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens"):
            total[key] += int(part.get(key) or 0)
    return total
