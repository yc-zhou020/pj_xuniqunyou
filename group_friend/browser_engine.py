"""Independent browser sampling and durable delivery. Model calls never own the UI lock."""
import asyncio
from dataclasses import dataclass
import json
from pathlib import Path
import time

from .conversation_snapshot import ChatMessage, ConversationSnapshot, ContinuityError, digest
from .runtime_support import validate_reply
from .browser_errors import BrowserUIError, ManualActionRequired


class BrowserEngine:
    def __init__(self, settings, config, store, adapter, request_reply, logger, *, clock=time.time, stop=None):
        self.settings, self.config, self.store, self.adapter = settings, config, store, adapter
        self.request_reply, self.logger, self.clock = request_reply, logger, clock
        self.stop = stop or asyncio.Event()
        self.ui_lock = asyncio.Lock()
        self.samples = self.sent = 0
        self.last_error = None

    def event(self, status, **data):
        self.logger.info(json.dumps(dict(status=status, **data), ensure_ascii=False))

    def paused(self):
        return self.stop.is_set() or Path(self.config.pause_path).exists()

    async def read(self, friend):
        async with self.ui_lock:
            await self.adapter.select(friend)
            return await self.adapter.read(friend)

    def sample(self, friend, messages, *, reply_latest=False):
        added, baseline = self.store.ingest(friend, messages, reply_latest=reply_latest)
        self.samples += 1
        folder = self.settings.get('snapshot_dir')
        if folder:
            directory = Path(folder)
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / (digest(friend['sec_uid'])[:24] + '.json')
            temp = destination.with_suffix('.tmp')
            temp.write_text(ConversationSnapshot(friend, self.clock(), tuple(messages), ()).dumps(), encoding='utf-8')
            temp.replace(destination)
        self.event('baseline' if baseline else 'sampled', target=friend['name'], messages=len(messages), queued=added,
                   delivery_blocked=self.store.blocked())
        return added

    async def step(self):
        if self.paused():
            return False
        if self.store.blocked():
            if self.last_error != 'unresolved_send':
                self.event('delivery_blocked', reason='unresolved_send')
                self.last_error = 'unresolved_send'
            return False
        self.last_error = None
        job = self.store.next_job()
        if job is None:
            return False
        snapshot = ConversationSnapshot.loads(job['snapshot'])
        friend = snapshot.friend
        if friend not in self.settings['whitelist']:
            self.store.mark(job['id'], 'cancelled_whitelist')
            self.event('cancelled_whitelist')
            return True
        try:
            current = await self.read(friend)
            known = self.store.known_outgoing(job['target'])
            if snapshot.has_manual_reply(current, known):
                self.store.mark(job['id'], 'cancelled_manual')
                self.event('cancelled_manual', target=friend['name'])
                return True
            if job['status'] == 'pending':
                fresh = snapshot.refreshed(current, self.clock())
                self.store.mark(job['id'], 'generating', snapshot=fresh)
                try:
                    reply = await self.request_reply(fresh)
                    reply = validate_reply(' '.join(reply.splitlines())[:min(self.config.max_reply_chars - 5, 295)])
                except Exception as exc:
                    attempts = job['attempts'] + 1
                    failed = attempts >= self.settings.get('max_model_attempts', 3)
                    self.store.mark(job['id'], 'failed' if failed else 'pending', delay=min(60, 5 * 2 ** (attempts - 1)))
                    self.event('model_failed' if failed else 'model_retry', target=friend['name'],
                               attempts=attempts, reason=type(exc).__name__)
                    return True
                self.store.mark(job['id'], 'generated', reply=reply)
                self.event('generated', target=friend['name'], job=job['id'])
                job = self.store.job(job['id'])
                snapshot = fresh
            if self.paused():
                return False
            if self.config.dry_run:
                self.store.mark(job['id'], 'dry_run')
                self.event('dry_run', target=friend['name'])
                return True
            due = self.store.next_send_time(job['target'], self.config, self.settings['min_send_interval_seconds'])
            if due > self.clock():
                self.store.mark(job['id'], 'generated', delay=due - self.clock())
                self.event('send_deferred', target=friend['name'], wait_seconds=round(due - self.clock(), 3))
                return True
            # Atomic UI preflight + typing/click. Sampling is free during model calls.
            async with self.ui_lock:
                await self.adapter.select(friend)
                current = await self.adapter.read(friend)
                if snapshot.has_manual_reply(current, self.store.known_outgoing(job['target'])):
                    self.store.mark(job['id'], 'cancelled_manual')
                    self.event('cancelled_manual', target=friend['name'])
                    return True
                await self.adapter.preflight(friend, snapshot, current, job['reply'])
                evidence = await self.adapter.prepare(friend, snapshot, job['reply'])
                if self.paused():
                    return False
                if self.store.blocked():
                    return False
                self.store.mark(job['id'], 'sending')
                try:
                    await self.adapter.submit(friend, snapshot, job['reply'], evidence)
                except BaseException:
                    self.store.mark(job['id'], 'unknown')
                    self.event('unknown', target=friend['name'], stage='submit')
                    raise
            # Confirmation takes short read locks, allowing the sampler to continue.
            deadline = asyncio.get_running_loop().time() + 12
            while True:
                try:
                    async with self.ui_lock:
                        await self.adapter.select(friend)
                        current = await self.adapter.read(friend)
                        confirmed = await self.adapter.confirm(friend, job['reply'], evidence, current)
                    if confirmed:
                        self.store.confirmed(job['id'], current)
                        self.sent += 1
                        self.event('visible_sent', target=friend['name'], stage='confirmed', job=job['id'])
                        return True
                except Exception:
                    pass  # A transient post-click read failure never triggers another click.
                if asyncio.get_running_loop().time() >= deadline:
                    self.store.mark(job['id'], 'unknown')
                    self.event('unknown', target=friend['name'], stage='confirmation')
                    return True
                await asyncio.sleep(.4)
        except ContinuityError as exc:
            status = self.store.job(job['id'])['status']
            if status not in ('sending', 'unknown', 'visible_sent'):
                self.store.mark(job['id'], 'cancelled_source')
                self.event('cancelled_source', target=friend['name'], reason=str(exc))
            return True

    async def sampler(self, interval):
        loop = asyncio.get_running_loop()
        due = loop.time()
        first = True
        failures = 0
        while not self.paused():
            try:
                for friend in self.settings['whitelist']:
                    async with self.ui_lock:
                        await self.adapter.select(friend)
                        messages = await self.adapter.read(friend)
                    self.sample(friend, messages, reply_latest=first and self.settings.get('reply_latest', False))
                failures = 0
            except asyncio.CancelledError:
                raise
            except ContinuityError as exc:
                # Preserve the accepted cursor rather than treating loaded history as new.
                self.event('observation_waiting', reason=str(exc))
            except ManualActionRequired as exc:
                self.event('manual_action_required', reason=str(exc))
                failures = 0
            except Exception as exc:
                failures += 1
                self.event('sample_retry', failures=failures, reason=type(exc).__name__)
                if failures >= 3:
                    try:
                        async with self.ui_lock:
                            await self.adapter.recover()
                        self.event('browser_recovered')
                        failures = 0
                    except Exception as recovery:
                        self.event('browser_recovery_wait', reason=type(recovery).__name__)
            first = False
            due += interval
            now = loop.time()
            if due < now:
                missed = int((now - due) // interval) + 1
                due += missed * interval
                self.event('sample_lag', missed=missed)
            await self.wait(max(0, due - loop.time()))

    async def dispatcher(self):
        while not self.paused():
            try:
                worked = await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.event('delivery_retry', reason=str(exc) if isinstance(exc, BrowserUIError) else type(exc).__name__)
                job = self.store.next_job()
                if job:
                    self.store.mark(job['id'], job['status'], delay=5)
                worked = False
            await self.wait(.05 if worked else .25)

    async def wait(self, seconds):
        deadline = asyncio.get_running_loop().time() + seconds
        while not self.paused():
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return
            try:
                await asyncio.wait_for(self.stop.wait(), min(.25, remaining))
            except asyncio.TimeoutError:
                pass


@dataclass(frozen=True)
class PreparedSend:
    before: int
    button: object


class PageAdapter:
    def __init__(self, session):
        self.session = session

    async def select(self, friend):
        await self.session.select(friend)

    async def read(self, friend):
        return [ChatMessage.from_browser(m) for m in await self.session.read(friend)]

    async def recover(self):
        await self.session.close()
        await self.session.start()

    async def editor(self):
        from .browser_reference.page_selectors import MESSAGE_INPUTS
        for selector in MESSAGE_INPUTS:
            locator = self.session.page.locator(selector)
            visible = [locator.nth(i) for i in range(await locator.count()) if await locator.nth(i).is_visible()]
            if len(visible) > 1:
                raise BrowserUIError('editor_not_unique')
            if visible and await visible[0].evaluate("el => el.isContentEditable || ['INPUT','TEXTAREA'].includes(el.tagName)"):
                return visible[0]
        raise BrowserUIError('editor_unavailable')

    async def button(self):
        from .browser_reference.page_selectors import SEND_BUTTONS
        for selector in SEND_BUTTONS:
            locator = self.session.page.locator(selector)
            visible = [locator.nth(i) for i in range(await locator.count()) if await locator.nth(i).is_visible()]
            if len(visible) > 1:
                raise BrowserUIError('send_button_not_unique')
            if visible:
                return visible[0]
        raise BrowserUIError('send_button_unavailable')

    async def draft(self):
        editor = await self.editor()
        return await editor.evaluate("el => String(el.isContentEditable ? el.innerText : el.value).replace(/\\u200b/g,'').trim()")

    async def preflight(self, friend, snapshot, current, reply):
        snapshot.locate(current)
        if await self.draft():
            raise BrowserUIError('existing_draft_preserved')
        await self.button()

    async def prepare(self, friend, snapshot, reply):
        # Preparation never clicks send. Its failures leave a generated job,
        # rather than falsely claiming that delivery may already have happened.
        current = await self.read(friend)
        snapshot.locate(current)
        before = sum(m.direction == 'outgoing' and m.text == reply for m in current)
        if await self.draft():
            raise BrowserUIError('existing_draft_preserved')
        editor = await self.editor()
        # Firefox fill() fires insertCompositionText for the entire string;
        # Douyin's Slate/editor-kit applies that text twice. Follow the reference's
        # keyboard path, preserving full equality checks before the single click.
        await editor.click(timeout=3000)
        await editor.press_sequentially(reply, delay=0, timeout=5000)
        await asyncio.sleep(.15)
        if await self.draft() != reply:
            raise BrowserUIError('exact_draft_not_confirmed')
        await self.session.chat.open_friend(friend, exact=True, current_only=True)
        snapshot.locate(await self.read(friend))
        button = await self.button()
        if not await button.is_enabled():
            raise BrowserUIError('send_button_disabled')
        if await self.draft() != reply:
            raise BrowserUIError('exact_draft_not_confirmed')
        return PreparedSend(before, button)

    async def submit(self, friend, snapshot, reply, prepared):
        # No Enter fallback and no click retry. Persist sending immediately before
        # this method; any click failure is still quarantined as unknown.
        await prepared.button.click(timeout=3000, no_wait_after=True)

    async def confirm(self, friend, reply, prepared, current):
        return not await self.draft() and sum(m.direction == 'outgoing' and m.text == reply for m in current) == prepared.before + 1
