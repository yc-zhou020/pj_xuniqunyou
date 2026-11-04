"""Dedicated Firefox profile with manual login and verified browser identity."""
import json
import os
from pathlib import Path
import logging

from playwright.async_api import async_playwright

from .browser_reference.douyin_chat import DouyinChat, FriendMismatch, FriendNotFound
from .browser_errors import ManualActionRequired, BrowserUIError


class BrowserSession:
    def __init__(self, settings):
        self.settings = settings
        self.driver = self.context = self.page = self.chat = None
        self.stage = 'not_started'

    async def start(self):
        try:
            return await self._start()
        except BrowserUIError:
            raise
        except Exception as exc:
            raise BrowserUIError('browser_' + self.stage + '_' + type(exc).__name__) from None

    async def _start(self):
        self.stage = 'launching'
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(self.settings['browser_path']).resolve())
        logging.getLogger('douyin.chat').disabled = True
        self.driver = await async_playwright().start()
        self.context = await self.driver.firefox.launch_persistent_context(
            str(Path(self.settings['profile_dir']).resolve()), headless=self.settings.get('headless', False),
            no_viewport=True,
            timeout=45000, firefox_user_prefs={'browser.sessionstore.resume_from_crash': False,
                                              'browser.startup.page': 0, 'browser.tabs.warnOnClose': False})
        self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
        self.page.set_default_timeout(5000)
        self.chat = DouyinChat(self.page, timeout_ms=5000)
        self.stage = 'loading_chat'
        await self.page.goto('https://www.douyin.com/chat', wait_until='domcontentloaded', timeout=45000)
        if not self.settings.get('manual_login'):
            state = await self.chat.login_state_settled()
            if state in ('login_required', 'risk'):
                raise ManualActionRequired(state)
        self.stage = 'ready'
        return self

    async def close(self):
        try:
            if self.context:
                await self.context.close()
        finally:
            try:
                if self.driver:
                    await self.driver.stop()
            finally:
                self.driver = self.context = self.page = self.chat = None

    async def select(self, friend):
        if not friend.get('douyin_id') or not friend.get('sec_uid'):
            raise FriendMismatch('whitelist_identity_required')
        try:
            current = await self.chat.current_panel_identity()
            if (current and str(current.get('sec_uid') or '') == friend['sec_uid']
                    and str(current.get('unique_id') or current.get('short_id') or '') == friend['douyin_id']):
                return current
            # Use the reference's strict row path, not its nickname-only cold-open
            # fallback: row participant ID and returned profile must both match.
            matches = []
            for row in await self.chat._rows_by_title(friend['name'], exact=True):
                if await self.chat._row_participant_sec_uid(row) == friend['sec_uid']:
                    matches.append(row)
            if len(matches) != 1:
                raise FriendMismatch('whitelist_row_identity_not_unique')
            user = await self.chat.open_friend(friend, exact=True, row=matches[0])
            if str(user.get('sec_uid') or '') != friend['sec_uid']:
                raise FriendMismatch('selected_profile_identity_changed')
            return user
        except (FriendMismatch, FriendNotFound):
            state = await self.chat.login_state()
            if state in ('login_required', 'risk'):
                raise ManualActionRequired(state) from None
            raise

    async def read(self, friend):
        await self.chat.open_friend(friend, exact=True, current_only=True)
        # Upstream is newest first. Our snapshots and cursor are chronological.
        return list(reversed(await self.chat.read_messages(limit=200)))

    async def bind_visible_friend(self, name):
        rows = await self.chat._rows_by_title(name, exact=True)
        if len(rows) != 1:
            raise FriendMismatch('friend_name_not_unique_or_not_loaded')
        user = await self.chat.identify_row(rows[0], name)
        current = await self.chat.current_panel_identity()
        if not user or not current or user.get('sec_uid') != current.get('sec_uid'):
            raise FriendMismatch('friend_identity_not_confirmed')
        actual = str(current.get('unique_id') or current.get('short_id') or '').strip()
        if not actual or not current.get('sec_uid') or await self.chat.open_panel_title() != name:
            raise FriendMismatch('friend_identity_not_confirmed')
        return dict(name=name, douyin_id=actual, sec_uid=str(current['sec_uid']))


def load_settings(path):
    settings = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if settings.get('backend') != 'browser':
        raise ValueError('browser_backend_required')
    if not isinstance(settings.get('whitelist'), list) or not settings['whitelist']:
        raise ValueError('whitelist_required')
    if any(not isinstance(f, dict) or not isinstance(f.get('name'), str) or not f['name'].strip()
           for f in settings['whitelist']):
        raise ValueError('invalid_whitelist')
    if len({f['name'] for f in settings['whitelist']}) != len(settings['whitelist']):
        raise ValueError('duplicate_whitelist_name')
    for field in ('sample_interval_seconds', 'min_send_interval_seconds'):
        if type(settings.get(field)) not in (int, float) or not 1 <= settings[field] <= 3600:
            raise ValueError('invalid_timing')
    if type(settings.get('headless', True)) is not bool:
        raise ValueError('invalid_headless')
    for field, default, maximum in (('context_messages', 7, 200), ('max_model_attempts', 3, 10), ('retention_days', 7, 365)):
        value = settings.get(field, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError('invalid_' + field)
    for field in ('reply_config', 'profile_dir', 'browser_path', 'state_path', 'snapshot_dir', 'log_path'):
        if not isinstance(settings.get(field), str) or not settings[field].strip():
            raise ValueError('invalid_' + field)
    return settings
