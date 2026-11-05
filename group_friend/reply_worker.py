"""Browser-free model worker: durable pending snapshots in, generated replies out."""
import asyncio
import json
from pathlib import Path
import time

from .conversation_snapshot import ConversationSnapshot
from .runtime_support import validate_reply, strip_ai_prefix
from .targets import allowed_snapshot


class ReplyWorker:
    def __init__(self, settings, config, store, request_reply, logger, *, clock=time.time, stop=None):
        self.settings, self.config, self.store = settings, config, store
        self.request_reply, self.logger, self.clock = request_reply, logger, clock
        self.stop = stop or asyncio.Event()

    def paused(self):
        return self.stop.is_set() or Path(self.config.pause_path).exists()

    def event(self, status, **data):
        self.logger.info(json.dumps(dict(status=status, **data), ensure_ascii=False))

    async def step(self):
        if self.paused():
            return False
        job = self.store.next_job('pending')
        if job is None:
            return False
        snapshot = ConversationSnapshot.loads(job['snapshot'])
        friend = snapshot.friend
        if not allowed_snapshot(snapshot, self.settings):
            self.store.mark(job['id'], 'cancelled_whitelist')
            return True
        if snapshot.has_manual_reply(snapshot.messages, self.store.known_outgoing(job['target'])):
            self.store.mark(job['id'], 'cancelled_manual')
            return True
        self.store.mark(job['id'], 'generating')
        try:
            reply = await self.request_reply(snapshot)
            limit = min(self.config.max_reply_chars, 300)
            reply = strip_ai_prefix(' '.join(reply.splitlines()))
            reply = validate_reply(reply[:limit], limit=limit)
        except asyncio.CancelledError:
            self.store.mark(job['id'], 'pending')
            raise
        except Exception as exc:
            attempts = job['attempts'] + 1
            failed = attempts >= self.settings.get('max_model_attempts', 3)
            self.store.mark(job['id'], 'failed' if failed else 'pending',
                            delay=min(60, 5 * 2 ** (attempts - 1)))
            self.event('model_failed' if failed else 'model_retry', target=friend['name'],
                       attempts=attempts, reason=type(exc).__name__)
            return True
        self.store.mark(job['id'], 'generated', reply=reply)
        self.event('generated', target=friend['name'], job=job['id'])
        if self.config.dry_run:
            # A later live sender must never deliver a mock/dry-run artifact.
            self.store.mark(job['id'], 'dry_run')
            self.event('dry_run', target=friend['name'])
        return True

    async def run(self):
        while not self.paused():
            worked = await self.step()
            try:
                await asyncio.wait_for(self.stop.wait(), .05 if worked else .25)
            except asyncio.TimeoutError:
                pass
