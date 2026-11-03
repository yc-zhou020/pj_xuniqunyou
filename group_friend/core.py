import json
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .memory import MEMORY_DATA_RULE, MemoryStorageError, augment_context


@dataclass(frozen=True)
class Message:
    platform: str
    conversation_id: str
    message_id: str
    sender_id: str | None
    sender_name: str
    text: str
    timestamp: float
    is_self: bool | None = None


class Transport(Protocol):
    def verify_target(self, conversation_id: str) -> bool: ...
    def send(self, conversation_id: str, text: str) -> str:
        """Return sent/failed/unknown. Exceptions are treated as unknown."""
        ...


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
            platform TEXT, conversation TEXT, id TEXT, sender TEXT,
            status TEXT NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY(platform, conversation, id))""")
        # Crash after entering send cannot establish whether delivery happened.
        with self.db:
            self.db.execute("UPDATE messages SET status='unknown' WHERE status='sending'")
            self.db.execute("UPDATE messages SET status='failed' WHERE status IN ('received','generated')")

    def claim(self, message, now):
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO messages VALUES(?,?,?,?,?,?)", (
                message.platform, message.conversation_id, message.message_id,
                message.sender_id, "received", now))
        return cursor.rowcount == 1

    def mark(self, message, status, now):
        with self.db:
            self.db.execute("UPDATE messages SET status=?, updated=? WHERE platform=? AND conversation=? AND id=?",
                            (status, now, message.platform, message.conversation_id, message.message_id))

    def blocked(self, platform, conversation):
        return self.db.execute("SELECT 1 FROM messages WHERE platform=? AND conversation=? AND status='unknown' LIMIT 1",
                               (platform, conversation)).fetchone() is not None

    def recent(self, platform, conversation, since, sender=None):
        sql = "SELECT COUNT(*) FROM messages WHERE platform=? AND conversation=? AND updated>=? AND status IN ('sent','sending','unknown','dry_run')"
        args = [platform, conversation, since]
        if sender is not None:
            sql += " AND sender=?"
            args.append(sender)
        return self.db.execute(sql, args).fetchone()[0]

    def close(self):
        self.db.close()


class Engine:
    def __init__(self, config, store, model, transport, *, started_at=None, clock=time.time, memory=None):
        self.config, self.store, self.model, self.transport = config, store, model, transport
        self.clock = clock
        self.started_at = clock() if started_at is None else started_at
        self.context = deque(maxlen=config.context_messages)
        self.memory = memory if config.memory_enabled else None
        self.memory_status = "enabled" if self.memory is not None else "disabled"
        self.recalled_memory_ids = []
        self.persona = Path(config.persona_path).read_text(encoding="utf-8")
        self.paused = False
        # One synchronous runner; reject concurrent use of this instance.
        self.lock = threading.Lock()

    def is_paused(self):
        return self.paused or Path(self.config.pause_path).exists()

    def handle(self, message):
        with self.lock:
            return self._handle(message)

    def _handle(self, m):
        c, now = self.config, self.clock()
        self.recalled_memory_ids = []
        if m.conversation_id != c.target_conversation:
            return "wrong_conversation", None
        if not m.platform or not m.message_id or not isinstance(m.text, str):
            return "invalid_message", None
        if m.is_self is True or m.sender_id == c.self_identity:
            return "self", None
        if m.is_self is not False or not m.sender_id:
            return "unknown_sender", None
        if not isinstance(m.timestamp, (int, float)) or not self.started_at <= m.timestamp <= now + 5:
            return "history_or_invalid_time", None
        if self.is_paused():
            return "paused", None
        if self.store.blocked(m.platform, m.conversation_id):
            return "unresolved_send", None
        if not self.store.claim(m, now):
            return "duplicate", None
        entry = {"role": "user", "content": json.dumps({
            "sender": m.sender_name, "text": m.text[:c.context_chars]}, ensure_ascii=False)}
        self.context.append(entry)
        memory_space = None
        memory_source = json.dumps([m.platform, m.conversation_id, m.message_id], ensure_ascii=False)
        if self.memory is not None:
            try:
                memory_space = self.memory.space_for_conversation(m.platform, m.conversation_id)
                self.memory.add(memory_space, m.text[:16000], actor=m.sender_name, source_key=memory_source)
                self.memory_status = "stored"
            except (MemoryStorageError, sqlite3.Error, OSError):
                self.memory_status = "storage_unavailable"
        def finish(status, text=None):
            self.store.mark(m, status, self.clock())
            return status, text
        if not (m.text == c.trigger_prefix or m.text.startswith(c.trigger_prefix + " ")):
            return finish("not_triggered")
        if (self.store.recent(m.platform, m.conversation_id, now - c.cooldown_seconds)
                or self.store.recent(m.platform, m.conversation_id, now - c.user_cooldown_seconds, m.sender_id)
                or self.store.recent(m.platform, m.conversation_id, now - 3600) >= c.hourly_limit
                or self.store.recent(m.platform, m.conversation_id, now - 86400) >= c.daily_limit):
            return finish("rate_limited")
        context, remaining = [], c.context_chars
        for item in reversed(self.context):
            if len(item["content"]) > remaining:
                if not context:
                    context.append({"role": "user", "content": item["content"][:remaining]})
                break
            context.append(item)
            remaining -= len(item["content"])
        try:
            messages = list(reversed(context))
            persona = self.persona
            self.recalled_memory_ids = []
            if self.memory is not None and memory_space is not None:
                try:
                    query = m.text[len(c.trigger_prefix):].strip()
                    messages, self.recalled_memory_ids = augment_context(
                        self.memory, memory_space, query, messages, c, exclude_source=memory_source)
                    if self.recalled_memory_ids:
                        persona += MEMORY_DATA_RULE
                except (MemoryStorageError, sqlite3.Error, OSError):
                    self.memory_status = "recall_unavailable"
            reply = self.model.reply(persona, messages)
            if not isinstance(reply, str) or not reply.strip():
                return finish("failed")
            reply = reply.strip()[:c.max_reply_chars]
            if not reply.startswith("[AI]"):
                reply = "[AI] " + reply[:max(0, c.max_reply_chars - 5)]
        except Exception:
            return finish("failed")
        self.store.mark(m, "generated", self.clock())
        if self.is_paused():
            return finish("paused")
        if c.dry_run:
            return finish("dry_run", reply)
        try:
            if not self.transport.verify_target(m.conversation_id):
                self.paused = True
                return finish("target_unverified")
        except Exception:
            self.paused = True
            return finish("target_unverified")
        if self.is_paused():
            return finish("paused")
        self.store.mark(m, "sending", self.clock())
        try:
            result = self.transport.send(m.conversation_id, reply)
            if result not in ("sent", "failed", "unknown"):
                result = "unknown"
        except Exception:
            result = "unknown"
        if result == "unknown":
            self.paused = True
        if result == "sent" and self.memory is not None and memory_space is not None:
            try:
                self.memory.add(memory_space, reply, kind="assistant", source_key=json.dumps(
                    ["assistant", m.platform, m.conversation_id, m.message_id], ensure_ascii=False))
            except (MemoryStorageError, sqlite3.Error, OSError):
                self.memory_status = "storage_unavailable"
        return finish(result)
