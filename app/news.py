"""Headlines from an allowlist of public RSS feeds. No key, no open web fetch."""

from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from app.config import ROOT
from app.safety import screen_untrusted

FEEDS = (
    ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
    ("BBC US & Canada", "https://feeds.bbci.co.uk/news/world/us_and_canada/rss.xml"),
    ("NPR News", "https://feeds.npr.org/1001/rss.xml"),
)
ALLOWED_HOSTS = {"feeds.bbci.co.uk", "feeds.npr.org"}
TTL_SECONDS = 30 * 60
FALLBACK_PATH = ROOT / "data" / "fallback_headlines.json"

_cache: dict = {"at": 0.0, "payload": None}


def _hid(link: str) -> str:
    return "h" + hashlib.sha1(link.encode("utf-8")).hexdigest()[:8]


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", text or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:360]


def _parse_rss(xml_text: str, source: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        summary = _strip_html(item.findtext("description") or "")
        published = (item.findtext("pubDate") or "").strip()
        title, title_flag = screen_untrusted(title)
        summary, summary_flag = screen_untrusted(summary)
        if not title or not link or title_flag or summary_flag:
            continue
        if not title:
            continue
        items.append(
            {
                "id": _hid(link),
                "source": source,
                "title": title,
                "summary": summary,
                "url": link,
                "published": published,
            }
        )
        if len(items) >= 4:
            break
    return items


def _fetch_live() -> dict | None:
    headlines: list[dict] = []
    seen: set[str] = set()
    headers = {"User-Agent": "CallTime/1.0 (MGT 409E class project; rehearsal room)"}
    with httpx.Client(timeout=8.0, follow_redirects=False, headers=headers) as client:
        for source, url in FEEDS:
            host = httpx.URL(url).host
            if host not in ALLOWED_HOSTS:
                continue
            try:
                response = client.get(url)
            except httpx.HTTPError:
                continue
            if response.status_code != 200:
                continue
            try:
                batch = _parse_rss(response.text, source)
            except ET.ParseError:
                continue
            for item in batch:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])
                headlines.append(item)
    if len(headlines) < 3:
        return None
    return {
        "practice_set": False,
        "fetched_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "headlines": headlines[:8],
    }


def _fallback() -> dict | None:
    if not FALLBACK_PATH.exists():
        return None
    try:
        data = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    headlines = []
    for item in data.get("headlines") or []:
        title, flagged = screen_untrusted(item.get("title") or "")
        summary, summary_flagged = screen_untrusted(item.get("summary") or "")
        if flagged or summary_flagged or not title:
            continue
        headlines.append({**item, "title": title, "summary": summary})
    if len(headlines) < 3:
        return None
    return {
        "practice_set": True,
        "fetched_at": data.get("fetched_at") or "undated",
        "headlines": headlines[:8],
    }


def get_headlines(*, force: bool = False) -> dict:
    now = time.time()
    if not force and _cache["payload"] and now - _cache["at"] < TTL_SECONDS:
        return _cache["payload"]
    live = _fetch_live()
    if live:
        _cache["at"] = now
        _cache["payload"] = live
        return live
    saved = _fallback()
    if saved:
        _cache["at"] = now
        _cache["payload"] = saved
        return saved
    return {
        "practice_set": True,
        "fetched_at": None,
        "headlines": [],
        "error": "The news feeds did not answer, and there is no saved practice set. Call Time will not invent headlines.",
    }


def headline_by_id(packet: dict, headline_id: str) -> dict | None:
    for item in packet.get("headlines") or []:
        if item["id"] == headline_id:
            return item
    return None


def save_snapshot(packet: dict) -> None:
    """Write a practice set the repo can keep. Only call this on purpose."""
    if packet.get("practice_set") or not packet.get("headlines"):
        return
    payload = {
        "fetched_at": packet.get("fetched_at"),
        "practice_set": True,
        "note": "Saved from the live feeds so a room with no network can still rehearse. The app labels this as a practice set.",
        "headlines": packet["headlines"],
    }
    FALLBACK_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
