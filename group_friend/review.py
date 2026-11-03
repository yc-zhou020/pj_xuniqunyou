"""Persistent human-reviewed replies. Approval is tied to text and session binding."""
import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import asdict
from pathlib import Path


class ReviewError(RuntimeError):
    pass


def binding_key(binding):
    return hashlib.sha256(json.dumps(asdict(binding), sort_keys=True).encode()).hexdigest()


def text_hash(text):
    return hashlib.sha256(text.encode()).hexdigest()


def validate_reply(text, limit=300):
    if not isinstance(text, str) or not text.strip():
        raise ReviewError("empty_reply")
    text = text.strip()
    if not text.startswith("[AI]"):
        text = "[AI] " + text
    if len(text) > limit or any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 for c in text):
        raise ReviewError("reply_must_be_single_line_within_limit")
    return text


class ReviewQueue:
    def __init__(self, path="state/replies.sqlite3", *, clock=time.time):
        self.clock = clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=5)
        self.db.row_factory = sqlite3.Row
        self.db.execute("""CREATE TABLE IF NOT EXISTS replies (
            id TEXT PRIMARY KEY, state TEXT NOT NULL, revision INTEGER NOT NULL,
            binding TEXT NOT NULL, binding_key TEXT NOT NULL,
            source TEXT NOT NULL, reply TEXT NOT NULL, approved_hash TEXT,
            updated REAL NOT NULL, approved_at REAL, outcome TEXT
        )""")

    def close(self):
        self.db.close()

    def get(self, item_id):
        row = self.db.execute("SELECT * FROM replies WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise ReviewError("reply_not_found")
        result = dict(row)
        result["binding"] = json.loads(result["binding"])
        return result

    def list(self):
        return [dict(row) for row in self.db.execute(
            "SELECT id,state,revision,updated FROM replies ORDER BY updated DESC")]

    def enqueue(self, binding, source_text, reply):
        reply = validate_reply(reply)
        if not isinstance(source_text, str) or not source_text.strip():
            raise ReviewError("source_text_required")
        item_id = str(uuid.uuid4())
        with self.db:
            self.db.execute("INSERT INTO replies VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                item_id, "pending", 1, json.dumps(asdict(binding), ensure_ascii=False),
                binding_key(binding), source_text, reply, None, self.clock(), None, None))
        return item_id

    def edit(self, item_id, revision, text):
        text = validate_reply(text)
        with self.db:
            cursor = self.db.execute("""UPDATE replies SET reply=?, revision=revision+1,
                state='pending', approved_hash=NULL, approved_at=NULL, updated=?
                WHERE id=? AND revision=? AND state IN ('pending','approved')""",
                (text, self.clock(), item_id, revision))
            if cursor.rowcount != 1:
                raise ReviewError("revision_changed_or_reply_not_editable")

    def discard(self, item_id, revision):
        with self.db:
            cursor = self.db.execute("""UPDATE replies SET state='discarded', reply='', source='',
                approved_hash=NULL, updated=? WHERE id=? AND revision=?
                AND state IN ('pending','approved')""", (self.clock(), item_id, revision))
            if cursor.rowcount != 1:
                raise ReviewError("revision_changed_or_reply_not_discardable")

    def approve(self, item_id, revision, *, target_heading, binding):
        with self.db:
            item = self.get(item_id)
            if item["binding_key"] != binding_key(binding) or target_heading != item["binding"]["group_heading"]:
                raise ReviewError("target_or_session_changed")
            cursor = self.db.execute("""UPDATE replies SET state='approved', approved_hash=?,
                approved_at=?, updated=? WHERE id=? AND revision=? AND state IN ('pending','approved')""",
                (text_hash(item["reply"]), self.clock(), self.clock(), item_id, revision))
            if cursor.rowcount != 1:
                raise ReviewError("revision_changed_or_reply_not_pending")

    def claim_send(self, item_id, revision, binding, *, approval_ttl=600,
                   cooldown_seconds=30, hourly_limit=10, daily_limit=30):
        # Atomic across processes; unresolved sends block all sessions so a rebind
        # cannot silently bypass an ambiguous delivery.
        try:
            self.db.execute("BEGIN IMMEDIATE")
            if self.db.execute("SELECT 1 FROM replies WHERE state IN ('sending','unknown') LIMIT 1").fetchone():
                raise ReviewError("unresolved_send_requires_manual_check")
            item = self.get(item_id)
            if item["state"] != "approved" or item["revision"] != revision:
                raise ReviewError("reply_not_approved_at_this_revision")
            if item["binding_key"] != binding_key(binding):
                raise ReviewError("session_changed_recreate_reply")
            if item["approved_hash"] != text_hash(item["reply"]):
                raise ReviewError("approved_text_changed")
            if not 0 <= self.clock() - item["approved_at"] <= approval_ttl:
                raise ReviewError("approval_expired")
            for seconds, limit in ((cooldown_seconds, 1), (3600, hourly_limit), (86400, daily_limit)):
                count = self.db.execute("SELECT COUNT(*) FROM replies WHERE state='visible_sent' AND updated>=?",
                                        (self.clock() - seconds,)).fetchone()[0]
                if count >= limit:
                    raise ReviewError("review_send_rate_limited")
            self.db.execute("UPDATE replies SET state='sending',updated=? WHERE id=?", (self.clock(), item_id))
            self.db.commit()
            return item
        except BaseException:
            self.db.rollback()
            raise

    def finish_send(self, item_id, outcome):
        if outcome not in ("visible_sent", "not_sent", "unknown"):
            outcome = "unknown"
        with self.db:
            cursor = self.db.execute("UPDATE replies SET state=?,outcome=?,updated=? WHERE id=? AND state='sending'",
                                     (outcome, outcome, self.clock(), item_id))
            if cursor.rowcount != 1:
                raise ReviewError("send_state_changed")

    def resolve(self, item_id, outcome, note):
        if outcome not in ("visible_sent", "not_sent") or not note.strip():
            raise ReviewError("manual_check_note_required")
        with self.db:
            cursor = self.db.execute("""UPDATE replies SET state=?,outcome=?,updated=? WHERE id=?
                AND (state='unknown' OR (state='sending' AND updated<=?))""",
                (outcome, "manual: " + note[:500], self.clock(), item_id, self.clock() - 90))
            if cursor.rowcount != 1:
                raise ReviewError("reply_not_unresolved_or_send_still_active")

    def purge(self, days):
        if not isinstance(days, int) or days < 0:
            raise ReviewError("invalid_retention_days")
        with self.db:
            cursor = self.db.execute("""DELETE FROM replies WHERE state IN
                ('discarded','visible_sent','not_sent') AND updated<=?""", (self.clock() - days * 86400,))
        return cursor.rowcount


def send_reviewed(queue, item_id, revision, binding, transport, pause_path, config=None):
    if config is not None and config.dry_run:
        raise ReviewError("dry_run_enabled_use_explicit_local_send_config")
    if Path(pause_path).exists():
        raise ReviewError("paused")
    # This preflight must be read-only. The backend repeats its checks after claim.
    item = queue.get(item_id)
    transport.preflight(binding, item["source"])
    if Path(pause_path).exists():
        raise ReviewError("paused")
    limits = {} if config is None else dict(cooldown_seconds=config.cooldown_seconds,
                                           hourly_limit=config.hourly_limit, daily_limit=config.daily_limit)
    item = queue.claim_send(item_id, revision, binding, **limits)
    try:
        if Path(pause_path).exists():
            outcome = "not_sent"
        else:
            outcome = transport.send(binding, item["source"], item["reply"], pause_path)
    except BaseException:
        queue.finish_send(item_id, "unknown")
        raise
    queue.finish_send(item_id, outcome)
    return queue.get(item_id)["state"]
