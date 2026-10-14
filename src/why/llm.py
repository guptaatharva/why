"""Nemotron on Nebius Token Factory (OpenAI-compatible).

Three roles, per the PRD:
    nano   high-volume cheap work: mining history, classifying results
    super  writing guard tests
    ultra  judging conflicts and root causes

Configuration (env):
    NEBIUS_API_KEY          required for model calls; without it Why runs its rule-based fallbacks
    WHY_LLM_BASE_URL        default https://api.tokenfactory.nebius.com/v1
    WHY_MODEL_NANO/SUPER/ULTRA  exact model ids; if unset, resolved from /models by name
    WHY_OFFLINE=1           never call a model
"""

from __future__ import annotations

import json
import os
import re
import time
from functools import lru_cache
from typing import Any

import httpx

BASE_URL = "https://api.tokenfactory.nebius.com/v1"

# Substrings used to pick each role's model from the provider's /models list
# when no exact id is configured. First match wins.
ROLE_HINTS = {
    "nano": ["nemotron-nano", "nemotron_nano", "nano"],
    "super": ["nemotron-super", "nemotron_super", "super"],
    "ultra": ["nemotron-ultra", "nemotron_ultra", "ultra"],
}
FALLBACK_IDS = {
    "nano": "nvidia/NVIDIA-Nemotron-Nano-9B-v2",
    "super": "nvidia/Llama-3_3-Nemotron-Super-49B-v1_5",
    "ultra": "nvidia/Llama-3_1-Nemotron-Ultra-253B-v1",
}


class LLMError(RuntimeError):
    pass


def api_key() -> str | None:
    return os.environ.get("NEBIUS_API_KEY") or os.environ.get("WHY_LLM_API_KEY")


def available() -> bool:
    return bool(api_key()) and os.environ.get("WHY_OFFLINE") not in ("1", "true", "yes")


def base_url() -> str:
    return os.environ.get("WHY_LLM_BASE_URL", BASE_URL).rstrip("/")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=base_url(),
        headers={"Authorization": f"Bearer {api_key()}"},
        timeout=httpx.Timeout(180.0, connect=15.0),
    )


@lru_cache(maxsize=1)
def list_models() -> list[str]:
    with _client() as c:
        r = c.get("/models")
        r.raise_for_status()
        return [m["id"] for m in r.json().get("data", [])]


def model_for(role: str) -> str:
    explicit = os.environ.get(f"WHY_MODEL_{role.upper()}")
    if explicit:
        return explicit
    try:
        ids = list_models()
    except Exception:
        return FALLBACK_IDS[role]
    nemotron = [i for i in ids if "nemotron" in i.lower()]
    for hint in ROLE_HINTS[role]:
        for i in nemotron:
            if hint in i.lower():
                return i
    return FALLBACK_IDS[role]


def chat(role: str, system: str, user: str, *, max_tokens: int = 2048, temperature: float = 0.2) -> str:
    if not available():
        raise LLMError("No model available (set NEBIUS_API_KEY, unset WHY_OFFLINE)")
    payload = {
        "model": model_for(role),
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    last: Exception | None = None
    for attempt in range(3):
        try:
            with _client() as c:
                r = c.post("/chat/completions", json=payload)
            if r.status_code in (429, 500, 502, 503, 504):
                raise LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            text = r.json()["choices"][0]["message"].get("content") or ""
            return strip_thinking(text)
        except (httpx.HTTPError, LLMError, KeyError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise LLMError(f"{role} model call failed: {last}")


def chat_json(role: str, system: str, user: str, **kw: Any) -> Any:
    text = chat(role, system + "\nRespond with JSON only, no prose.", user, **kw)
    return extract_json(text)


def strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def extract_json(text: str) -> Any:
    text = strip_thinking(text)
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    for opener, closer in (("[", "]"), ("{", "}")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError(f"Model did not return JSON: {text[:300]}")


def extract_code(text: str) -> str:
    text = strip_thinking(text)
    blocks = re.findall(r"```(?:python|py|javascript|js|ts|typescript)?\s*\n(.*?)```", text, flags=re.S)
    return (max(blocks, key=len) if blocks else text).strip() + "\n"
