"""
LLM client for Groq API — all API keys loaded from settings, NEVER hardcoded.
Preserved call_groq + extract_json logic from timetable_app.py.
"""
from __future__ import annotations
import json
import re
import requests
from app.core.config import get_settings

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
REASONING_MODELS = {"openai/gpt-oss-120b"}
MAX_TOKENS = {"openai/gpt-oss-120b": 20000, "llama-3.3-70b-versatile": 16000}


def call_groq(messages: list, preferred_model: str | None = None, expect_json: bool = True) -> tuple[str, str]:
    """
    Try each model × key combination in order. Returns (raw_content, model_used).
    Keys are loaded from settings — no hardcoded credentials.
    """
    settings = get_settings()
    keys = settings.groq_key_list
    if not keys:
        raise RuntimeError("No Groq API keys configured. Set GROQ_API_KEYS in .env")

    models = [settings.GROQ_MODEL]
    if preferred_model:
        models = [preferred_model] + [m for m in models if m != preferred_model]

    last_error = "no attempt made"
    for model in models:
        for key in keys:
            key = key.strip()
            if not key:
                continue
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": MAX_TOKENS.get(model, 8000),
            }
            if expect_json:
                payload["response_format"] = {"type": "json_object"}
            if model in REASONING_MODELS:
                payload["include_reasoning"] = False
            try:
                r = requests.post(
                    GROQ_URL,
                    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                    json=payload,
                    timeout=180,
                )
                if r.status_code == 200:
                    return r.json()["choices"][0]["message"]["content"], model
                last_error = f"{model} key=...{key[-6:]} http={r.status_code} {r.text[:300]}"
            except Exception as e:
                last_error = f"{model} key=...{key[-6:]} {e}"

    raise RuntimeError(f"All Groq keys and models failed. Last error: {last_error}")


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
