"""Where the app looks for keys, and which model it is allowed to call.

The course key may only run gpt-6-luna. The model name is fixed here on purpose.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Repo folder first, then the folder above it (where the teaching staff puts .env).
# override=False means a key already set in the repo wins, and the parent fills gaps.
load_dotenv(ROOT / ".env", override=False)
load_dotenv(ROOT.parent / ".env", override=False)

MODEL_NAME = "gpt-6-luna"

# A contestant session stops here. A shared day stops here. Both are real brakes.
SESSION_USD = 0.75
DAILY_USD = 5.00
HOUR_RUNS = 12


def key_is_set() -> bool:
    return bool(os.getenv("OPENAI_API_KEY", "").strip() or os.getenv("PORTKEY_API_KEY", "").strip())


def require_api_key() -> tuple[str, str]:
    """Prefer the OpenAI key. Fall back to Portkey only if that is all that is set."""
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if openai_key:
        return "openai", openai_key
    portkey_key = os.getenv("PORTKEY_API_KEY", "").strip()
    if portkey_key:
        return "portkey", portkey_key
    raise RuntimeError(
        "OPENAI_API_KEY is missing. Put it in .env in this project, "
        "or in the folder one level above the repo."
    )


@lru_cache(maxsize=1)
def build_model():
    """gpt-6-luna needs the Responses API when tools are involved."""
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    which, api_key = require_api_key()
    if which == "openai":
        client = AsyncOpenAI(api_key=api_key)
    else:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1").rstrip("/"),
            default_headers={"x-portkey-provider": os.getenv("PORTKEY_PROVIDER", "openai")},
        )
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def model_settings():
    """Reasoning effort must stay off or luna rejects function tools."""
    from pydantic_ai.models.openai import OpenAIResponsesModelSettings

    return OpenAIResponsesModelSettings(openai_reasoning_effort="none")
