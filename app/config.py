"""Centralized config. Every threshold, path, and mode flag lives here."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass


ROOT = Path(__file__).resolve().parent.parent


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    # Execution
    mode: str = os.getenv("MODE", "hybrid").lower()
    llm_provider: str = os.getenv("LLM_PROVIDER", "groq").lower()
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.2")
    max_concurrent_tickets: int = _env_int("MAX_CONCURRENT", 5)

    # Failure injection
    chaos_rate: float = _env_float("CHAOS", 0.08)
    tool_timeout_seconds: float = _env_float("TOOL_TIMEOUT", 3.0)
    max_retries: int = _env_int("MAX_RETRIES", 2)
    retry_base_delay: float = _env_float("RETRY_BASE_DELAY", 0.25)

    # LLM retry/repair
    llm_repair_attempts: int = _env_int("LLM_REPAIR_ATTEMPTS", 1)
    llm_max_rate_limit_retries: int = _env_int("LLM_MAX_429_RETRIES", 3)

    # Confidence thresholds (dual)
    escalation_threshold: float = _env_float("ESCALATION_THRESHOLD", 0.7)
    refund_confidence_floor: float = _env_float("REFUND_CONFIDENCE_FLOOR", 0.85)

    # Policy
    refund_escalation_amount: float = _env_float("REFUND_ESCALATE_AMOUNT", 200.0)

    # Deterministic
    seed: int = _env_int("SEED", 42)
    today: str = os.getenv("TODAY", "2024-03-15")

    # Paths (absolute, rooted at repo)
    tickets_path: str = str(ROOT / "data" / "tickets.json")
    customers_path: str = str(ROOT / "data" / "customers.json")
    orders_path: str = str(ROOT / "data" / "orders.json")
    products_path: str = str(ROOT / "data" / "products.json")
    knowledge_base_path: str = str(ROOT / "data" / "knowledge-base.md")
    audit_log_path: str = str(ROOT / "audit_log.json")
    dlq_path: str = str(ROOT / "dead_letter_queue.json")


CONFIG = Config()
