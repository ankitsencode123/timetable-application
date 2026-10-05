"""
LLM client — supports Groq (primary) and OpenRouter (fallback).
All API keys loaded from settings, NEVER hardcoded.
"""
from __future__ import annotations
import json
import re
import requests
from app.core.config import get_settings

GROQ_URL        = "https://api.groq.com/openai/v1/chat/completions"
OPENROUTER_URL  = "https://openrouter.ai/api/v1/chat/completions"
REASONING_MODELS = {"openai/gpt-oss-120b"}
MAX_TOKENS = {"openai/gpt-oss-120b": 20000, "llama-3.3-70b-versatile": 16000}


def _call_provider(
    url: str,
    api_key: str,
    model: str,
    messages: list,
    expect_json: bool = True,
    extra_headers: dict | None = None,
) -> str | None:
    """POST to an OpenAI-compatible endpoint. Returns content string or None on failure."""
    payload: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "max_tokens": MAX_TOKENS.get(model, 8000),
    }
    if expect_json:
        payload["response_format"] = {"type": "json_object"}
    if model in REASONING_MODELS:
        payload["include_reasoning"] = False

    headers = {
        "Authorization": "Bearer " + api_key,
        "Content-Type": "application/json",
    }
    if extra_headers:
        headers.update(extra_headers)

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=180)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception:
        pass
    return None


def call_groq(messages: list, preferred_model: str | None = None, expect_json: bool = True) -> tuple[str, str]:
    """
    Calls OpenRouter exclusively, as requested by the user.
    The function name remains call_groq to maintain compatibility with existing imports.
    """
    settings = get_settings()
    
    or_key = settings.openrouter_key
    if not or_key:
        raise RuntimeError("No OpenRouter API key configured. Set OPENROUTER_API_KEY in .env")

    or_model = preferred_model if preferred_model else settings.OPENROUTER_MODEL
    
    # OpenRouter requires HTTP-Referer and X-Title for free models
    extra = {
        "HTTP-Referer": "https://timetable-app.onrender.com",
        "X-Title": "Timetable Application",
    }
    
    content = _call_provider(
        OPENROUTER_URL, or_key, or_model, messages,
        expect_json=expect_json,
        extra_headers=extra,
    )
    
    if content is not None:
        return content, or_model
        
    raise RuntimeError(f"OpenRouter LLM request failed for model {or_model}.")


def extract_json(text: str) -> dict:
    """Parse JSON from model output, stripping markdown fences and trailing commas."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z0-9]*", "", t).strip()
        if t.endswith("```"):
            t = t[:-3].strip()
    start = t.find("{")
    end = t.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("no JSON object found in model output")
    candidate = t[start:end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        candidate2 = re.sub(r",\s*([}\]])", r"\1", candidate)
        return json.loads(candidate2)
