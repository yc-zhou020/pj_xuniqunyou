import json
import os
from dataclasses import dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit


def load_env(path: str = ".env") -> None:
    if not Path(path).exists():
        return
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not key.strip().isidentifier():
            raise ValueError("Invalid .env entry")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


@dataclass(frozen=True)
class Config:
    target_conversation: str
    self_identity: str
    trigger_prefix: str = "/ai"
    dry_run: bool = True
    cooldown_seconds: float = 30
    user_cooldown_seconds: float = 60
    hourly_limit: int = 10
    daily_limit: int = 30
    context_messages: int = 12
    context_chars: int = 6000
    max_reply_chars: int = 300
    timeout_seconds: float = 30
    api_retries: int = 1
    state_path: str = "state/messages.sqlite3"
    pause_path: str = "state/PAUSE"
    persona_path: str = "persona.txt"
    memory_enabled: bool = True
    memory_path: str = "state/long_term_memory.sqlite3"
    memory_capacity_mb: int = 256
    memory_retention_days: int = 180
    memory_context_chars: int = 2000
    memory_context_items: int = 6

    def __post_init__(self):
        for name in ("target_conversation", "self_identity", "trigger_prefix"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} must be a nonempty string")
        if type(self.dry_run) is not bool:
            raise ValueError("dry_run must be boolean")
        if type(self.memory_enabled) is not bool:
            raise ValueError("memory_enabled must be boolean")
        for name in ("memory_capacity_mb", "memory_retention_days", "memory_context_chars", "memory_context_items"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.memory_capacity_mb > 65536 or self.memory_context_items > 50:
            raise ValueError("Memory capacity/items exceed supported limits")
        for name in ("hourly_limit", "daily_limit", "context_messages", "context_chars", "max_reply_chars"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("cooldown_seconds", "user_cooldown_seconds", "timeout_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 <= value < float("inf"):
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.max_reply_chars < 6:
            raise ValueError("max_reply_chars must be at least 6 to fit the AI label")
        if self.timeout_seconds == 0 or type(self.api_retries) is not int or not 0 <= self.api_retries <= 2:
            raise ValueError("Invalid timeout or api_retries (0–2)")

    @classmethod
    def load(cls, path: str):
        raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        return cls(**raw)


def api_settings():
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip()
    url = urlsplit(base)
    if not key or not model:
        raise ValueError("Set DEEPSEEK_API_KEY and DEEPSEEK_MODEL locally")
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("DEEPSEEK_BASE_URL must be an HTTPS URL without credentials/query/fragment")
    return key, base, model
