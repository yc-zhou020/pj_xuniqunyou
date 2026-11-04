"""Durable browser sampling cursors, pending work and conservative send receipts."""
import json
from pathlib import Path
import sqlite3
import time

from .conversation_snapshot import ConversationSnapshot, digest, overlap_size, ContinuityError


class BrowserState:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self._outgoing_cache = {}
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS browser_cursors(target TEXT PRIMARY KEY, keys TEXT NOT NULL, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS browser_jobs(
                id TEXT PRIMARY KEY,target TEXT NOT NULL,status TEXT NOT NULL,
                snapshot TEXT,reply TEXT,attempts INTEGER NOT NULL DEFAULT 0,
                due REAL NOT NULL,updated REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS browser_job_due ON browser_jobs(status,due);
            CREATE INDEX IF NOT EXISTS browser_job_time ON browser_jobs(status,updated);
            CREATE INDEX IF NOT EXISTS browser_target_time ON browser_jobs(target,status,updated);
            CREATE TABLE IF NOT EXISTS browser_outgoing(target TEXT,key TEXT,updated REAL,PRIMARY KEY(target,key));
        ''')
        with self.db:
            self.db.execute("UPDATE browser_jobs SET status='unknown' WHERE status='sending'")
            self.db.execute("UPDATE browser_jobs SET status='pending' WHERE status='generating'")

    def close(self):
        self.db.close()

    def blocked(self):
        for table, column in (('browser_jobs', 'status'), ('auto_jobs', 'status'), ('replies', 'state')):
            if self.db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                if self.db.execute(f"SELECT 1 FROM {table} WHERE {column} IN ('sending','unknown') LIMIT 1").fetchone():
                    return True
        return False

    def ingest(self, friend, messages, *, reply_latest=False):
        target = friend['sec_uid']
        keys = [m.key for m in messages]
        previous = self.db.execute('SELECT keys FROM browser_cursors WHERE target=?', (target,)).fetchone()
        old = json.loads(previous[0]) if previous else None
        if old and not keys:
            raise ContinuityError('transient_empty_list')
        if old is None or not old:
            indices = [len(messages) - 1] if reply_latest and messages else []
        else:
            overlap = overlap_size(old, keys)
            indices = list(range(overlap, len(messages)))
        indices = [i for i in indices if messages[i].direction == 'incoming' and messages[i].kind == 'text'
                   and messages[i].text and not messages[i].text.lstrip().startswith('[AI]')]
        if self.db.execute("SELECT COUNT(*) FROM browser_jobs WHERE status IN ('pending','generating','generated')").fetchone()[0] >= 128:
            raise ContinuityError('pending_queue_full')
        added = 0
        with self.db:
            for start in range(0, len(indices), 10):
                selected = tuple(indices[start:start + 10])
                snapshot = ConversationSnapshot(friend, self.clock(), tuple(messages), selected)
                key = digest([target, keys, selected])
                cursor = self.db.execute('INSERT OR IGNORE INTO browser_jobs VALUES(?,?,?,?,?,0,?,?)',
                                         (key, target, 'pending', snapshot.dumps(), None, self.clock(), self.clock()))
                added += cursor.rowcount
            self.db.execute('INSERT OR REPLACE INTO browser_cursors VALUES(?,?,?)', (target, json.dumps(keys), self.clock()))
        return added, old is None

    def job(self, job_id):
        row = self.db.execute('SELECT * FROM browser_jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def next_job(self):
        row = self.db.execute("SELECT * FROM browser_jobs WHERE status IN ('pending','generated') AND due<=? ORDER BY rowid LIMIT 1",
                              (self.clock(),)).fetchone()
        return dict(row) if row else None

    def mark(self, job_id, status, *, reply=None, snapshot=None, delay=0):
        with self.db:
            self.db.execute('''UPDATE browser_jobs SET status=?,reply=COALESCE(?,reply),snapshot=COALESCE(?,snapshot),
                               due=?,updated=? WHERE id=?''',
                            (status, reply, snapshot.dumps() if snapshot else None,
                             self.clock() + delay, self.clock(), job_id))
            if status == 'generating':
                self.db.execute('UPDATE browser_jobs SET attempts=attempts+1 WHERE id=?', (job_id,))
            if status in ('visible_sent', 'cancelled_manual', 'cancelled_source', 'cancelled_whitelist', 'failed', 'dry_run', 'not_sent'):
                self.db.execute('UPDATE browser_jobs SET snapshot=NULL,reply=NULL WHERE id=?', (job_id,))

    def known_outgoing(self, target):
        if target not in self._outgoing_cache:
            self._outgoing_cache[target] = {row[0] for row in self.db.execute('SELECT key FROM browser_outgoing WHERE target=?', (target,))}
        return self._outgoing_cache[target]

    def confirmed(self, job_id, messages):
        job = self.job(job_id)
        with self.db:
            for message in messages:
                if message.direction == 'outgoing' and message.text == job['reply']:
                    self.db.execute('INSERT OR IGNORE INTO browser_outgoing VALUES(?,?,?)', (job['target'], message.key, self.clock()))
                    self.known_outgoing(job['target']).add(message.key)
            self.mark(job_id, 'visible_sent')

    def next_send_time(self, target, config, minimum_gap):
        now = self.clock()
        last = self.db.execute("SELECT MAX(updated) FROM browser_jobs WHERE status='visible_sent'").fetchone()[0]
        # Global spacing, even when the queue contains more than one friend.
        due = last + minimum_gap if last is not None else now
        for seconds, limit in ((3600, config.hourly_limit), (86400, config.daily_limit)):
            count = self.db.execute("SELECT COUNT(*) FROM browser_jobs WHERE target=? AND status='visible_sent' AND updated>?", (target, now - seconds)).fetchone()[0]
            if count >= limit:
                stamp = self.db.execute("SELECT updated FROM browser_jobs WHERE target=? AND status='visible_sent' AND updated>? ORDER BY updated DESC LIMIT 1 OFFSET ?",
                                        (target, now - seconds, limit - 1)).fetchone()[0]
                due = max(due, stamp + seconds)
        return max(now, due)

    def prune(self, days):
        cutoff = self.clock() - days * 86400
        with self.db:
            self.db.execute("DELETE FROM browser_jobs WHERE updated<? AND status IN ('visible_sent','cancelled_manual','cancelled_source','cancelled_whitelist','failed','dry_run','not_sent')", (cutoff,))
            self.db.execute('DELETE FROM browser_outgoing WHERE updated<?', (cutoff,))
        self._outgoing_cache.clear()
