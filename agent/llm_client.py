"""
Unified LLM client for the ShopWave agent.

Supports two providers selectable via the LLM_PROVIDER environment variable:

  LLM_PROVIDER=gemini   → Google Gemini (requires GEMINI_API_KEY)
                          Use this for online demo / GitHub submission.

  LLM_PROVIDER=ollama   → Local Ollama (no API key needed, must be running)
                          Use this for local development / offline use.
                          Requires: ollama pull llama3.2

Configuration (.env):
  LLM_PROVIDER=gemini          # or ollama
  GEMINI_API_KEY=AIza...       # only needed for gemini
  OLLAMA_MODEL=llama3.2        # optional, default llama3.2
  OLLAMA_BASE_URL=http://localhost:11434  # optional
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

_log = logging.getLogger(__name__)

# Single shared thread pool so LLM calls never block the event loop
_THREAD_POOL = ThreadPoolExecutor(max_workers=10)

# ── Provider selection ────────────────────────────────────────────────────────
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").lower().strip()

# ─────────────────────────────────────────────────────────────────────────────
# Internal: Google Gemini backend  (cached client instance)
# ─────────────────────────────────────────────────────────────────────────────

_GEMINI_CLIENT = None  # initialized once on first call


def _get_gemini_client():
    """Return a cached Gemini Client. Created only once per process."""
    global _GEMINI_CLIENT
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise EnvironmentError(
            "GEMINI_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey"
        )

    if _GEMINI_CLIENT is None:
        _GEMINI_CLIENT = genai.Client(api_key=api_key)
    return _GEMINI_CLIENT


def _gemini_chat(system: str, user_message: str, max_tokens: int = 1024) -> str:
    """Send a single-turn chat to Google Gemini. Returns the response text."""
    from google import genai

    client = _get_gemini_client()
    full_prompt = f"{system}\n\n{user_message}"
    model_name = os.environ.get("GEMINI_MODEL", "gemini-1.5-flash")
    
    response = client.models.generate_content(
        model=model_name,
        contents=full_prompt,
        config=genai.types.GenerateContentConfig(
            max_output_tokens=max_tokens,
            temperature=0.1,
        )
    )
    return response.text or ""


# ─────────────────────────────────────────────────────────────────────────────
# Internal: Ollama backend
# ─────────────────────────────────────────────────────────────────────────────

def _ollama_chat(system: str, user_message: str, max_tokens: int = 350) -> str:
    """
    Send a single-turn chat to a local Ollama instance. Returns the response text.
    Optimized for short, fast JSON responses.
    """
    import ollama as _ollama

    model = os.environ.get("OLLAMA_MODEL", "llama3.2")
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    # Optimization: Tight parameters for fast local inference
    options = {
        "num_predict": 250,
        "temperature": 0.0,
        "num_thread": 10,       # Still leveraging your 16 cores
        "num_ctx": 1024,
        "repeat_penalty": 1.0
    }

    # timeout=30 on the client forces the underlying HTTP request to abort —
    # asyncio.wait_for alone cannot interrupt a blocking thread in run_in_executor.
    client = _ollama.Client(host=base_url, timeout=60)
    try:
        # Note: sync client.chat is run inside a thread pool by llm_chat_async
        response = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
            options=options,
        )
        return response.message.content or ""
    except Exception as exc:
        _log.error("Ollama chat failed: %s", exc)
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Public API — used by all agent nodes
# ─────────────────────────────────────────────────────────────────────────────

def llm_chat(
    system: str,
    user_message: str,
    *,
    max_tokens: int = 350,
    provider: str | None = None,
) -> str:
    """
    Send a single-turn LLM chat using the configured provider.
    """
    p = (provider or LLM_PROVIDER).lower()
    try:
        if p == "gemini":
            return _gemini_chat(system, user_message, max_tokens)
        elif p == "ollama":
            return _ollama_chat(system, user_message, max_tokens)
        else:
            raise RuntimeError(f"Unknown LLM_PROVIDER='{p}'")
    except Exception as exc:
        _log.error("LLM call failed (provider=%s): %s", p, exc)
        raise


async def llm_chat_async(
    system: str,
    user_message: str,
    *,
    max_tokens: int = 350,
    provider: str | None = None,
    timeout: int = 35,
) -> str:
    """
    Async wrapper with a hard timeout to prevent the graph from hanging.
    """
    loop = asyncio.get_event_loop()
    try:
        return await asyncio.wait_for(
            loop.run_in_executor(
                _THREAD_POOL,
                lambda: llm_chat(system, user_message, max_tokens=max_tokens, provider=provider)
            ),
            timeout=timeout
        )
    except asyncio.TimeoutError:
        _log.warning("LLM call timed out after %ds", timeout)
        return '{"error": "timeout", "decision": "escalate", "reply": "Processing timeout. Escalated to human."}'
    except Exception as exc:
        _log.error("Async LLM call failed: %s", exc)
        return ""


def llm_json(
    system: str,
    user_message: str,
    *,
    max_tokens: int = 1024,
    provider: str | None = None,
) -> dict[str, Any]:
    """
    Like llm_chat() but automatically parses the response as JSON.
    Synchronous — use llm_json_async() inside async nodes.
    """
    raw = llm_chat(system, user_message, max_tokens=max_tokens, provider=provider)
    # Aggressive JSON extraction: find first { and last }
    match = re.search(r'(\{.*\})', raw, re.DOTALL)
    if match:
        cleaned = match.group(1)
    else:
        cleaned = raw.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        _log.warning("JSON parse failed: %s\nRaw response: %s", exc, raw[:300])
        return {}


async def llm_json_async(
    system: str,
    user_message: str,
    *,
    max_tokens: int = 1024,
    provider: str | None = None,
) -> dict[str, Any]:
    """
    Async version of llm_json — runs in thread pool so it never blocks
    the FastAPI event loop. Use this in all async agent nodes.
    """
    raw = await llm_chat_async(system, user_message, max_tokens=max_tokens, provider=provider)
    print(f"\n[DEBUG] RAW AI RESPONSE ({len(raw)} chars): {raw[:500]}...")

    # Robust JSON extraction: look for the first '{' and find the balance or last '}'
    start_index = raw.find('{')
    end_index = raw.rfind('}')
    if start_index != -1 and end_index != -1 and end_index > start_index:
        cleaned = raw[start_index:end_index+1]
    else:
        cleaned = raw.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        _log.warning("JSON parse failed: %s\nRaw response: %s", exc, raw[:300])
        return {}


def get_provider_info() -> dict[str, str]:
    """Return info about the active LLM provider (for logging/README)."""
    if LLM_PROVIDER == "gemini":
        return {
            "provider": "gemini",
            "model": os.environ.get("GEMINI_MODEL", "gemini-2.0-flash"),
            "api_key_set": "yes" if os.environ.get("GEMINI_API_KEY") else "NO — set GEMINI_API_KEY",
        }
    elif LLM_PROVIDER == "ollama":
        return {
            "provider": "ollama",
            "model": os.environ.get("OLLAMA_MODEL", "llama3.2"),
            "base_url": os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        }
    return {"provider": LLM_PROVIDER, "status": "unknown provider"}
