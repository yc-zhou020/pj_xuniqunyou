"""Durable browser sampling cursors, pending work and conservative send receipts."""
import json
from pathlib import Path
import sqlite3
import time

from .conversation_snapshot import ConversationSnapshot, digest, history_alignment, ContinuityError, observation_key
from .targets import is_group, target_key


class BrowserState:
    def __init__(self, path, clock=time.time, *, recover=('generate', 'send')):
        self.clock = clock
        if str(path) != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
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
            CREATE TABLE IF NOT EXISTS browser_observation_cursors(
                target TEXT PRIMARY KEY,cursor_digest TEXT NOT NULL,keys TEXT NOT NULL);
        ''')
        with self.db:
            if 'send' in recover:
                self.db.execute("UPDATE browser_jobs SET status='unknown' WHERE status='sending'")
            if 'generate' in recover:
                self.db.execute("UPDATE browser_jobs SET status='pending' WHERE status='generating'")

    def close(self):
        self.db.close()

    def blocked(self):
        for table, column in (('browser_jobs', 'status'), ('auto_jobs', 'status'), ('replies', 'state')):
            if self.db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                if self.db.execute(f"SELECT 1 FROM {table} WHERE {column} IN ('sending','unknown') LIMIT 1").fetchone():
                    return True
        return False

    def ingest(self, friend, messages, *, reply_latest=False, previous_messages=None):
        target = target_key(friend)
        keys = [m.key for m in messages]
        observed = [observation_key(m) for m in messages]
        previous = self.db.execute('SELECT keys FROM browser_cursors WHERE target=?', (target,)).fetchone()
        old = json.loads(previous[0]) if previous else None
        if old and not keys:
            raise ContinuityError('transient_empty_list')
        if old is None or not old:
            indices = [len(messages) - 1] if reply_latest and messages else []
        else:
            cached = self.db.execute('SELECT cursor_digest,keys FROM browser_observation_cursors WHERE target=?', (target,)).fetchone()
            if cached and cached['cursor_digest'] == digest(old):
                _, _, end = history_alignment(json.loads(cached['keys']), observed)
            elif previous_messages is not None and [m.key for m in previous_messages] == old:
                # Upgrade legacy cursors only from an exact matching accepted
                # snapshot; never infer media identities from a local hash.
                _, _, end = history_alignment([observation_key(m) for m in previous_messages], observed)
            else:
                _, _, end = history_alignment(old, keys)
            indices = list(range(end, len(messages)))
        indices = [i for i in indices if messages[i].direction == 'incoming' and messages[i].kind == 'text'
                   and messages[i].text]
        if is_group(friend):
            allowed = {member['user_id'] for member in friend['members'] if not member.get('is_self')}
            indices = [i for i in indices if messages[i].sender_id in allowed and messages[i].message_id]
        if self.db.execute("SELECT COUNT(*) FROM browser_jobs WHERE status IN ('pending','generating','generated')").fetchone()[0] >= 128:
            raise ContinuityError('pending_queue_full')
        added = 0
        with self.db:
            batches = []
            for index in indices:
                if (not batches or len(batches[-1]) >= 10
                        or (is_group(friend) and messages[batches[-1][-1]].sender_id != messages[index].sender_id)):
                    batches.append([])
                batches[-1].append(index)
            for batch in batches:
                selected = tuple(batch)
                snapshot = ConversationSnapshot(friend, self.clock(), tuple(messages), selected)
                key = digest([target, keys, selected])
                cursor = self.db.execute('INSERT OR IGNORE INTO browser_jobs VALUES(?,?,?,?,?,0,?,?)',
                                         (key, target, 'pending', snapshot.dumps(), None, self.clock(), self.clock()))
                added += cursor.rowcount
            self.db.execute('INSERT OR REPLACE INTO browser_cursors VALUES(?,?,?)', (target, json.dumps(keys), self.clock()))
            self.db.execute('INSERT OR REPLACE INTO browser_observation_cursors VALUES(?,?,?)',
                            (target, digest(keys), json.dumps(observed)))
        return added, old is None

    def job(self, job_id):
        row = self.db.execute('SELECT * FROM browser_jobs WHERE id=?', (job_id,)).fetchone()
        return dict(row) if row else None

    def next_job(self, status=None):
        statuses = (status,) if status else ('pending', 'generated')
        if any(value not in ('pending', 'generated') for value in statuses):
            raise ValueError('invalid_queue_status')
        placeholders = ','.join('?' for _ in statuses)
        # Preserve per-contact delivery order even when an older task is retrying
        # generation or waiting for its send quota. Other contacts may proceed.
        ordering = """AND NOT EXISTS (
            SELECT 1 FROM browser_jobs older WHERE older.target=j.target
            AND older.rowid<j.rowid AND older.status IN ('pending','generating','generated','sending','unknown')
        )""" if status == 'generated' else ''
        row = self.db.execute(f"""SELECT j.* FROM browser_jobs j
            WHERE j.status IN ({placeholders}) AND j.due<=? {ordering}
            ORDER BY j.rowid LIMIT 1""", (*statuses, self.clock())).fetchone()
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
        # Another stage process may have just confirmed a send.
        return {row[0] for row in self.db.execute('SELECT key FROM browser_outgoing WHERE target=?', (target,))}

    def confirmed(self, job_id, messages):
        job = self.job(job_id)
        with self.db:
            for message in messages:
                if message.direction == 'outgoing' and message.text == job['reply']:
                    self.db.execute('INSERT OR IGNORE INTO browser_outgoing VALUES(?,?,?)', (job['target'], message.key, self.clock()))
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
