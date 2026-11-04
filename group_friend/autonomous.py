"""Standalone, explicitly enabled whitelist private replies; no Codex dependency."""
import argparse
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import re
from pathlib import Path
import sqlite3
import sys
import time
from dataclasses import dataclass, replace

from .config import Config, api_settings, load_env
from .douyin import (Binding, ObservationError, SnapshotTracker, capture,
                     PRIVATE_TIME_LABEL, parse_private_texts, private_chat_layout,
                     private_message_base, select_window)
from .llm import DeepSeek, MockModel
from .review import binding_key, validate_reply
from .review_transport import WindowsReviewedTransport


@dataclass(frozen=True)
class PrivateObservation:
    text: str
    direction: str  # visual inference only; never a platform sender ID/is_self
    source_path: str

    @property
    def signature(self):
        return hashlib.sha256(json.dumps([self.direction, self.text]).encode()).hexdigest()


class PrivateTracker(SnapshotTracker):
    """A full preserved prefix identifies appended visual occurrences, even repeats.

    This is still an observation index, never a platform message ID. Sliding or
    edited history continues to use the conservative overlap checks.
    """
    def update(self, messages):
        signatures = [m.signature for m in messages]
        if (not self.stopped and self.previous and len(signatures) > len(self.previous)
                and signatures[:len(self.previous)] == self.previous):
            tail = messages[len(self.previous):]
            self.previous = signatures
            events = []
            for message in tail:
                self.sequence += 1
                events.append((f'{self.session_id}:{self.sequence}', message))
            return events
        return super().update(messages)


def read_private(report, binding):
    window = select_window(report, binding.window_handle)
    if window.get('is_offscreen') is not False:
        raise ObservationError('window_not_visible')
    if (window.get('process_id') != binding.process_id
            or window.get('process_started_at') != binding.process_started_at):
        raise ObservationError('client_restarted_rebind_required')
    nodes = window['result']['Nodes']
    by_path = {n['Path']: n for n in nodes}
    if len(by_path) != len(nodes) or by_path.get('root', {}).get('Name') != binding.window_title:
        raise ObservationError('window_changed')
    layout = private_chat_layout(nodes, binding.group_heading)
    if layout is None:
        raise ObservationError('not_whitelisted_private_pane')
    base, _ = layout
    base = private_message_base(nodes, base)
    bounds = by_path[base].get('Bounds')
    if (not isinstance(bounds, list) or len(bounds) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)
            or bounds[2] <= 0 or bounds[3] <= 0):
        raise ObservationError('private_geometry_unavailable')
    left, top, width, height = bounds
    messages = {m.source_path: m for m in parse_private_texts(nodes, base)}
    rows = [n for n in nodes if n['Path'].startswith(base + '/')
            and n['Path'].count('/') == base.count('/') + 1]
    rows.sort(key=lambda n: int(n['Path'].rsplit('/', 1)[1]), reverse=True)
    direction = 'unknown'
    result = []
    for row in rows:
        path = row['Path']
        avatars = [n for n in nodes if n['Path'].startswith(path + '/0/') and n.get('Role') == 40]
        if avatars:
            direction = 'unknown'
            if len(avatars) == 1:
                rect = avatars[0].get('Bounds') or []
                if len(rect) == 4 and rect[2] > 0 and rect[3] > 0:
                    center = rect[0] + rect[2] / 2
                    if left <= center < left + width * .25:
                        direction = 'incoming'
                    elif left + width * .75 < center <= left + width:
                        direction = 'outgoing'
        elif path not in messages:
            label = row.get('Name') or ''
            if not re.fullmatch(PRIVATE_TIME_LABEL, label):
                direction = 'unknown'
        if path in messages:
            result.append(PrivateObservation(messages[path].text, direction, path))
    return result


class RuntimeState:
    def __init__(self, path, clock=time.time):
        self.clock = clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('''CREATE TABLE IF NOT EXISTS auto_jobs (
            id TEXT PRIMARY KEY, target TEXT NOT NULL, status TEXT NOT NULL,
            source_hash TEXT NOT NULL, reply_hash TEXT, updated REAL NOT NULL)''')
        with self.db:
            self.db.execute("UPDATE auto_jobs SET status='unknown' WHERE status='sending'")
            self.db.execute("UPDATE auto_jobs SET status='interrupted' WHERE status IN ('generating','generated')")

    def blocked(self):
        if self.db.execute("SELECT 1 FROM auto_jobs WHERE status IN ('sending','unknown') LIMIT 1").fetchone():
            return True
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='browser_jobs'").fetchone():
            if self.db.execute("SELECT 1 FROM browser_jobs WHERE status IN ('sending','unknown') LIMIT 1").fetchone():
                return True
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='replies'").fetchone():
            return self.db.execute("SELECT 1 FROM replies WHERE state IN ('sending','unknown') LIMIT 1").fetchone() is not None
        return False

    def claim(self, key, target, text, *, retry_unsent=False):
        with self.db:
            cursor = self.db.execute('INSERT OR IGNORE INTO auto_jobs VALUES(?,?,?,?,?,?)',
                                     (key, target, 'generating', hashlib.sha256(text.encode()).hexdigest(), None, self.clock()))
            if cursor.rowcount == 0 and retry_unsent:
                # error is assigned only before sending; never reclaim unknown/sent.
                cursor = self.db.execute("UPDATE auto_jobs SET status='generating',updated=? WHERE id=? AND status='error'", (self.clock(), key))
        return cursor.rowcount == 1

    def mark(self, key, status, reply=None):
        with self.db:
            self.db.execute('UPDATE auto_jobs SET status=?,reply_hash=COALESCE(?,reply_hash),updated=? WHERE id=?',
                            (status, hashlib.sha256(reply.encode()).hexdigest() if reply else None, self.clock(), key))

    def limited(self, config):
        has_review = self.db.execute("SELECT 1 FROM sqlite_master WHERE name='replies'").fetchone() is not None
        for seconds, maximum in ((config.cooldown_seconds, 1), (3600, config.hourly_limit), (86400, config.daily_limit)):
            count = self.db.execute("SELECT COUNT(*) FROM auto_jobs WHERE status IN ('visible_sent','sending','unknown') AND updated>=?",
                                    (self.clock() - seconds,)).fetchone()[0]
            if has_review:
                count += self.db.execute("SELECT COUNT(*) FROM replies WHERE state='visible_sent' AND updated>=?", (self.clock() - seconds,)).fetchone()[0]
            if count >= maximum:
                return True
        return False

    def close(self):
        self.db.close()


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        try:
            if self.path.stat().st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('runtime_already_running') from None
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.close()


class PrivateRuntime:
    def __init__(self, config, bindings, state, model, transport, logger, *, capture_fn=capture,
                 binding_loader=Binding.load, clock=time.time, log_text=False, retry_unsent=False, window_manager=None):
        self.config, self.bindings, self.state = config, bindings, state
        self.model, self.transport, self.logger = model, transport, logger
        self.capture_fn, self.binding_loader, self.clock = capture_fn, binding_loader, clock
        self.log_text = log_text
        self.retry_unsent = retry_unsent
        self.window_manager = window_manager
        self.trackers = {}
        self.context = {}
        self.persona = Path(config.persona_path).read_text(encoding='utf-8')
        self.persona += '\nYou are replying in a private chat as an AI. No tools or actions are available. Do not claim to have viewed media.'

    def event(self, status, **fields):
        self.logger.info(json.dumps(dict(status=status, **fields), ensure_ascii=False))

    def paused(self):
        return Path(self.config.pause_path).exists()

    def observe(self, binding):
        if self.window_manager and self.window_manager.prepare(binding):
            self.event('window_restored_background', target=binding.group_heading)
        return read_private(self.capture_fn(binding.process_id), binding)

    def process(self, key, binding, path, message, anchor):
        if self.paused():
            raise ObservationError('paused')
        if self.state.blocked():
            raise ObservationError('unresolved_auto_send')
        if message.direction != 'incoming' or message.text.lstrip().startswith('[AI]'):
            return
        if not self.state.claim(key, binding.group_heading, message.text, retry_unsent=self.retry_unsent):
            self.event('already_processed', target=binding.group_heading)
            return
        if self.state.limited(self.config):
            self.state.mark(key, 'rate_limited')
            self.event('rate_limited', target=binding.group_heading)
            return
        try:
            history = self.context.setdefault(binding_key(binding), [])
            context = history[-self.config.context_messages:] + [{'role': 'user', 'content': message.text[:self.config.context_chars]}]
            remaining = self.config.context_chars
            bounded = []
            for item in reversed(context):
                if len(item['content']) > remaining:
                    break
                bounded.append(item)
                remaining -= len(item['content'])
            reply = self.model.reply(self.persona, list(reversed(bounded)))
            reply = validate_reply(' '.join(reply.splitlines())[:min(self.config.max_reply_chars - 5, 295)])
            self.state.mark(key, 'generated', reply)
            self.event('generated', target=binding.group_heading, **({'source': message.text, 'reply': reply} if self.log_text else {}))
            if self.paused():
                self.state.mark(key, 'paused')
                raise ObservationError('paused')
            if self.config.dry_run:
                self.state.mark(key, 'dry_run')
                self.event('dry_run', target=binding.group_heading)
                return
            if self.binding_loader(path) != binding:
                raise ObservationError('binding_changed')
            current = self.observe(binding)
            index = anchor['index']
            if ([m.text for m in current[:len(anchor['texts'])]] != anchor['texts']
                    or index >= len(current) or current[index].direction != 'incoming'):
                self.event('source_anchor_changed', target=binding.group_heading,
                           expected_messages=len(anchor['texts']), observed_messages=len(current), index=index)
                raise ObservationError('incoming_source_changed')
            if self.transport.check_private(binding, message.text, reply, self.config.pause_path, anchor) != 'ready':
                self.event('private_preflight_failed', target=binding.group_heading,
                           **getattr(self.transport, 'last_diagnostics', {}))
                raise ObservationError('private_preflight_not_ready')
            if self.paused():
                raise ObservationError('paused')
            self.state.mark(key, 'sending')
            previous_window = self.window_manager.foreground() if self.window_manager else None
            try:
                outcome = self.transport.send_private(binding, message.text, reply, self.config.pause_path, anchor)
            except BaseException:
                self.state.mark(key, 'unknown')
                raise
            finally:
                if self.window_manager:
                    try:
                        if self.window_manager.return_focus(binding, previous_window):
                            self.event('previous_window_restored', target=binding.group_heading)
                    except Exception:
                        self.event('previous_window_restore_failed', target=binding.group_heading)
            if outcome not in ('visible_sent', 'not_sent'):
                outcome = 'unknown'
            self.state.mark(key, outcome)
            self.event(outcome, target=binding.group_heading, **getattr(self.transport, 'last_diagnostics', {}))
            if outcome != 'visible_sent':
                raise ObservationError('auto_send_' + outcome)
            history.extend([{'role': 'user', 'content': message.text}, {'role': 'assistant', 'content': reply}])
            del history[:-self.config.context_messages]
        except BaseException:
            with self.state.db:
                self.state.db.execute("UPDATE auto_jobs SET status='error' WHERE id=? AND status IN ('generating','generated')", (key,))
            raise

    def tick(self, *, reply_latest=False):
        if self.paused():
            raise ObservationError('paused')
        if self.state.blocked():
            raise ObservationError('unresolved_auto_send')
        for path, binding in self.bindings:
            if self.binding_loader(path) != binding:
                raise ObservationError('binding_changed_restart_required')
            try:
                messages = self.observe(binding)
            except ObservationError as exc:
                if str(exc) in ('window_not_visible', 'not_whitelisted_private_pane', 'window_on_other_desktop'):
                    # Reset on absence: do not treat restored history as newly arrived.
                    self.trackers.pop(binding_key(binding), None)
                    self.event('waiting_for_visible_private_target', target=binding.group_heading, reason=str(exc))
                    continue
                raise
            scope = binding_key(binding)
            fresh = scope not in self.trackers
            tracker = self.trackers.setdefault(scope, PrivateTracker())
            try:
                events = tracker.update(messages)
            except ObservationError:
                self.event('observation_changed', target=binding.group_heading,
                           previous_messages=len(tracker.previous or []), observed_messages=len(messages),
                           directions={d: sum(m.direction == d for m in messages) for d in ('incoming', 'outgoing', 'unknown')})
                raise
            if fresh:
                self.event('baseline', target=binding.group_heading, messages=len(messages))
                if reply_latest and messages and messages[-1].direction == 'incoming':
                    occurrence = hashlib.sha256(json.dumps([m.signature for m in messages]).encode()).hexdigest()
                    events = [('bootstrap:' + scope + ':' + occurrence, messages[-1])]
            for event_id, message in events:
                index = next(i for i, m in enumerate(messages) if m.source_path == message.source_path)
                anchor = dict(texts=[m.text for m in messages], index=index)
                self.process(scope + ':' + event_id, binding, path, message, anchor)
                # Our own outgoing bubble enters the next observation baseline.


def make_logger(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('group_friend.autonomous')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout), RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')):
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
    return logger


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Standalone whitelist private replies; keep the selected private pane visible')
    parser.add_argument('--config', default='autonomous.local.json')
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--inspect', action='store_true', help='Check window management and baseline once without API calls or sending')
    parser.add_argument('--reply-latest', action='store_true', help='Explicitly handle the latest visible incoming text once at startup')
    parser.add_argument('--retry-unsent', action='store_true', help='With --once --reply-latest, retry a prior pre-send error; never retries unknown/sent')
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument('--status', action='store_true', help='Show recent job metadata without changing state')
    actions.add_argument('--resolve', metavar='JOB_ID', help='Resolve an uncertain send after manually checking the private chat')
    parser.add_argument('--outcome', choices=('visible_sent', 'not_sent'))
    parser.add_argument('--note', help='Required manual verification note for --resolve')
    args = parser.parse_args(argv)
    if args.retry_unsent and not (args.once and args.reply_latest):
        parser.error('--retry-unsent requires --once --reply-latest')
    if args.inspect and (args.reply_latest or args.retry_unsent):
        parser.error('--inspect cannot generate or retry replies')
    logger = None
    state = None
    try:
        root = json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        allowed = {'whitelist', 'reply_config', 'state_path', 'log_path', 'log_text', 'interval', 'allowed_api_base', 'window_policy'}
        if set(root) - allowed:
            raise ValueError('unknown_runtime_config')
        state_path = Path(root.get('state_path', 'state/replies.sqlite3'))
        if args.status or args.resolve:
            if not state_path.exists():
                print(json.dumps(dict(status='no_runtime_state')))
                return 0
            def inspect_state():
                db = sqlite3.connect(state_path.resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    db.row_factory = sqlite3.Row
                    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='auto_jobs'").fetchone():
                        return []
                    return [dict(row) for row in db.execute('SELECT id,target,status,updated FROM auto_jobs ORDER BY updated DESC LIMIT 20')]
                finally:
                    db.close()
            if args.status:
                print(json.dumps(dict(jobs=inspect_state()), ensure_ascii=False))
                return 0
            if not args.outcome or not args.note or not args.note.strip():
                raise ValueError('resolution_note_and_outcome_required')
            with InstanceLock('state/autonomous.lock'):
                db = sqlite3.connect(state_path)
                try:
                    with db:
                        changed = db.execute("UPDATE auto_jobs SET status=?,updated=? WHERE id=? AND status IN ('unknown','sending') AND updated<=?",
                                             (args.outcome, time.time(), args.resolve, time.time() - 90)).rowcount
                    if changed != 1:
                        raise ValueError('job_not_uncertain_or_send_too_recent')
                finally:
                    db.close()
                make_logger(root.get('log_path', 'state/autonomous.log')).info(json.dumps(dict(
                    status='manually_resolved', id=args.resolve, outcome=args.outcome, note=args.note[:500]), ensure_ascii=False))
            return 0
        entries = root['whitelist']
        if not isinstance(entries, list) or not entries:
            raise ValueError('empty_whitelist')
        bindings = []
        for entry in entries:
            if set(entry) != {'name', 'binding'} or not isinstance(entry['name'], str) or not entry['name'].strip():
                raise ValueError('invalid_whitelist')
            binding = Binding.load(entry['binding'])
            if binding.group_heading != entry['name']:
                raise ValueError('whitelist_binding_mismatch')
            bindings.append((entry['binding'], binding))
        if len({binding_key(b) for _, b in bindings}) != len(bindings):
            raise ValueError('duplicate_whitelist_binding')
        interval = root.get('interval', 5)
        if type(interval) not in (int, float) or not 2 <= interval <= 3600:
            raise ValueError('invalid_interval')
        if type(root.get('log_text', False)) is not bool:
            raise ValueError('invalid_log_text')
        config = Config.load(root['reply_config'])
        if args.inspect:
            config = replace(config, dry_run=True)
        if config.memory_enabled:
            raise ValueError('standalone_private_memory_must_be_disabled')
        if args.mock and not config.dry_run:
            raise ValueError('mock_requires_dry_run')
        if not args.inspect and not args.mock:
            load_env()
        if not (args.mock or args.inspect) and api_settings()[1] != root.get('allowed_api_base', 'https://api.deepseek.com'):
            raise ValueError('api_destination_changed')
        policy = root.get('window_policy', 'restore_background')
        if policy not in ('passive', 'restore_background'):
            raise ValueError('invalid_window_policy')
        from .window_manager import WindowManager
        logger = make_logger(root.get('log_path', 'state/autonomous.log'))
        with InstanceLock('state/autonomous.lock'):
            state = None if args.inspect else RuntimeState(state_path)
            runtime = PrivateRuntime(config, bindings, state, MockModel() if (args.mock or args.inspect) else DeepSeek(config),
                                     WindowsReviewedTransport(), logger, log_text=root.get('log_text', False),
                                     retry_unsent=args.retry_unsent,
                                     window_manager=WindowManager() if policy == 'restore_background' else None)
            runtime.event('started', whitelist=[b.group_heading for _, b in bindings], dry_run=config.dry_run,
                          api='disabled_inspection' if args.inspect else ('mock' if args.mock else api_settings()[1]),
                          window_policy=policy)
            if args.inspect:
                for _, binding in bindings:
                    messages = runtime.observe(binding)
                    runtime.event('inspection_ready', target=binding.group_heading, messages=len(messages),
                                  directions={d: sum(m.direction == d for m in messages) for d in ('incoming', 'outgoing', 'unknown')})
                return 0
            first = True
            while True:
                runtime.tick(reply_latest=args.reply_latest and first)
                first = False
                if args.once or args.inspect:
                    break
                deadline = time.monotonic() + interval
                while time.monotonic() < deadline:
                    if runtime.paused():
                        raise ObservationError('paused')
                    time.sleep(min(.25, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        if logger:
            logger.info('stopped_by_user')
    except Exception as exc:
        # Exception messages can contain private data/API bodies; report class only.
        if logger:
            logger.error(json.dumps(dict(status='stopped', reason=str(exc) if isinstance(exc, ObservationError) else type(exc).__name__)))
        else:
            print(json.dumps(dict(status='startup_failed', reason=type(exc).__name__)))
        return 1
    finally:
        if state:
            state.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
