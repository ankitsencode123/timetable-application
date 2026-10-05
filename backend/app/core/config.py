"""App-wide settings — loaded once at import time from environment / .env file."""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Database ────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite:///./dev.db"

    # ── JWT ─────────────────────────────────────────────────────────────────
    JWT_SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ── LLM / Groq ───────────────────────────────────────────────────────────
    GROQ_API_KEYS: str = ""          # comma-separated key pool
    GROQ_MODEL: str = "openai/gpt-oss-120b"

    # ── LLM / OpenRouter (fallback) ──────────────────────────────────────────
    OPENROUTER_API_KEY: str = ""     # single key
    OPENROUTER_MODEL: str = "nvidia/nemotron-3-ultra-550b-a55b:free"

    # ── CORS ─────────────────────────────────────────────────────────────────
    # Set to specific origin(s) in production, e.g. https://your-app.vercel.app
    CORS_ORIGINS: str = "*"

    # ── Admin seed (first-boot only) ─────────────────────────────────────────
    ADMIN_EMAIL: str = "admin@example.com"
    ADMIN_PASSWORD: str = "changeme"
    ADMIN_FULL_NAME: str = "Admin"

    # ── Demo teacher seed ─────────────────────────────────────────────────────
    # JSON array: '[{"email":"teacher@x.com","name":"Teacher Name"}]'
    SEED_TEACHERS: str = ""  
    DEMO_TEACHER_PASSWORD: str = "teacher123"  # overridden in .env

    # ── Login rate limiting / lockout ─────────────────────────────────────────
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    # ── Cookie settings ───────────────────────────────────────────────────────
    COOKIE_SECURE: bool = False    # set True in production (HTTPS)
    COOKIE_SAMESITE: str = "lax"   # lax | strict | none
    COOKIE_DOMAIN: str = ""        # leave empty for localhost

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Derived helpers ───────────────────────────────────────────────────────
    @property
    def groq_key_list(self) -> List[str]:
        return [k.strip() for k in self.GROQ_API_KEYS.split(",") if k.strip()]

    @property
    def openrouter_key(self) -> str:
        return self.OPENROUTER_API_KEY.strip()

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    return Settings()
