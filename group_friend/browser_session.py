"""Dedicated Firefox profile with manual login and verified browser identity."""
import json
import os
from pathlib import Path
import logging
import asyncio
import re

from playwright.async_api import async_playwright

from .browser_reference.douyin_chat import DouyinChat, FriendMismatch, FriendNotFound
from .browser_errors import ManualActionRequired, BrowserUIError
from .targets import is_group
from .group_memory_reader import GROUP_INFO
from .group_message_identity import GROUP_SENDERS, PRIVATE_BINDING
from .browser_guard import require_no_verification


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
        await require_no_verification(self.page)
        try:
            return await self._select(friend)
        except ManualActionRequired:
            raise
        except Exception:
            # Verification can appear while a click is waiting for actionability.
            # Translate its timeout into a manual action, never an automatic retry.
            await require_no_verification(self.page)
            raise

    async def _select(self, friend):
        if is_group(friend):
            return await self.select_group(friend)
        if friend.get('is_self'):
            raise FriendMismatch('self_private_reply_forbidden')
        if not friend.get('sec_uid') or not (friend.get('douyin_id') or friend.get('user_id')):
            raise FriendMismatch('whitelist_identity_required')
        if not friend.get('douyin_id'):
            try:
                return await self.verify_target(friend)
            except FriendMismatch:
                pass
            matches = [row for row in await self.chat._iter_rows()
                       if await self.chat._row_participant_sec_uid(row) == friend['sec_uid']]
            if not matches:
                raise FriendNotFound('whitelist_conversation_not_visible')
            if len(matches) != 1:
                raise FriendMismatch('whitelist_row_identity_not_unique')
            await self.chat._open_row(matches[0])
            for attempt in range(20):
                try:
                    return await self.verify_target(friend)
                except FriendMismatch:
                    if attempt == 19:
                        raise
                    await asyncio.sleep(.25)
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
            if not matches:
                raise FriendNotFound('whitelist_conversation_not_visible')
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
        await self.verify_target(friend)
        # Upstream is newest first. Our snapshots and cursor are chronological.
        messages = list(reversed(await self.chat.read_messages(limit=200)))
        if is_group(friend):
            identities = await self.page.locator('[class*="messageMessageBoxmessageBox"]').evaluate_all(GROUP_SENDERS)
            by_id = {}
            for identity in identities:
                if identity['group_id'] != friend['group_id'] or not identity['message_id']:
                    continue
                key = identity['message_id']
                if key in by_id and by_id[key] != identity:
                    raise BrowserUIError('group_message_identity_ambiguous')
                by_id[key] = identity
            names = {member['user_id']: member['name'] for member in friend['members']}
            for message in messages:
                identity = by_id.get(str(message.get('message_id') or ''))
                if not identity or not identity['sender_id']:
                    raise BrowserUIError('group_message_identity_unavailable')
                owner = self.settings.get('owner_user_id')
                message['from_me'] = ((identity['sender_id'] == owner) if owner and identity and identity['sender_id']
                                      else identity['mine'] if identity else None)
                message['sender_id'] = identity['sender_id'] if identity else None
                message['sender_name'] = names.get(message['sender_id'])
                if message['from_me'] is None:
                    raise BrowserUIError('group_message_direction_unavailable')
        await self.verify_target(friend)
        return messages

    async def verify_target(self, friend):
        """Verify the currently open panel without navigating or touching drafts."""
        await require_no_verification(self.page)
        if self.settings.get('owner_user_id'):
            header = self.page.locator('div.RightPanelHeadertitle')
            owner_binding = await header.evaluate(GROUP_INFO) if await header.count() == 1 else None
            if not owner_binding or owner_binding['owner_user_id'] != self.settings['owner_user_id']:
                raise FriendMismatch('logged_in_account_changed')
        if is_group(friend):
            header = self.page.locator('div.RightPanelHeadertitle')
            if await header.count() != 1:
                raise FriendMismatch('group_header_unavailable')
            title = ' '.join((await header.inner_text()).split())
            if not re.fullmatch(re.escape(friend['name']) + r'(?:\(\d+\)|（\d+）)?', title):
                raise FriendMismatch('group_title_changed')
            binding = await header.evaluate(GROUP_INFO)
            if not binding or binding['type'] != 2 or binding['id'] != friend['group_id']:
                raise FriendMismatch('group_identity_changed')
            if self.settings.get('owner_user_id') and binding['owner_user_id'] != self.settings['owner_user_id']:
                raise FriendMismatch('logged_in_account_changed')
            return binding
        if friend.get('is_self'):
            raise FriendMismatch('self_private_reply_forbidden')
        if friend.get('douyin_id'):
            user = await self.chat.open_friend(friend, exact=True, current_only=True)
            if str(user.get('sec_uid') or '') != friend['sec_uid']:
                raise FriendMismatch('selected_profile_identity_changed')
            return user
        headers = self.page.locator('div.RightPanelHeadertitle')
        binding = await headers.evaluate(PRIVATE_BINDING) if await headers.count() == 1 else None
        if (not binding or binding['type'] != 1 or binding['sec_uid'] != friend['sec_uid']
                or binding['user_id'] != friend.get('user_id')):
            raise FriendMismatch('private_native_identity_changed')
        if self.settings.get('owner_user_id') and binding['owner_user_id'] != self.settings['owner_user_id']:
            raise FriendMismatch('logged_in_account_changed')
        return binding

    async def select_group(self, group):
        try:
            return await self.verify_target(group)
        except FriendMismatch:
            pass
        candidates = []
        for row in await self.chat._rows_by_title(group['name'], exact=True):
            binding = await row.evaluate(GROUP_INFO)
            if binding and binding['type'] == 2 and binding['id'] == group['group_id']:
                candidates.append(row)
        if not candidates:
            raise FriendNotFound('whitelist_group_not_visible')
        if len(candidates) != 1:
            raise FriendMismatch('group_row_identity_not_unique')
        await self.chat._open_row(candidates[0])
        for attempt in range(20):
            try:
                return await self.verify_target(group)
            except FriendMismatch:
                if attempt == 19:
                    raise
                await asyncio.sleep(.25)

    async def bind_visible_friend(self, name):
        await require_no_verification(self.page)
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
    settings.setdefault('whitelist', [])
    if not isinstance(settings['whitelist'], list) or (not settings['whitelist'] and not settings.get('group_whitelist')):
        raise ValueError('whitelist_required')
    if any(not isinstance(f, dict) or not isinstance(f.get('name'), str) or not f['name'].strip()
           for f in settings['whitelist']):
        raise ValueError('invalid_whitelist')
    if len({f['name'] for f in settings['whitelist']}) != len(settings['whitelist']):
        raise ValueError('duplicate_whitelist_name')
    for friend in settings['whitelist']:
        if (type(friend.get('is_self', False)) is not bool or type(friend.get('enabled', True)) is not bool
                or ('user_id' in friend and (not isinstance(friend['user_id'], str) or not friend['user_id'].isdigit()))):
            raise ValueError('invalid_private_whitelist_identity')
    if settings.get('owner_user_id') and (not isinstance(settings['owner_user_id'], str) or not settings['owner_user_id'].isdigit()):
        raise ValueError('invalid_owner_user_id')
    groups = settings.get('group_whitelist', [])
    if not isinstance(groups, list):
        raise ValueError('invalid_group_whitelist')
    ids = set()
    for group in groups:
        if (not isinstance(group, dict) or group.get('kind') != 'group'
                or not isinstance(group.get('name'), str) or not group['name'].strip()
                or not isinstance(group.get('group_id'), str) or not group['group_id'].isdigit()
                or type(group.get('enabled', True)) is not bool or not isinstance(group.get('members'), list)
                or not group['members'] or group['group_id'] in ids):
            raise ValueError('invalid_group_whitelist')
        ids.add(group['group_id'])
        users = set()
        for member in group['members']:
            if (not isinstance(member, dict) or not member.get('name') or not member.get('sec_uid')
                    or not isinstance(member.get('user_id'), str) or not member['user_id'].isdigit()
                    or member['user_id'] in users or type(member.get('is_self', False)) is not bool):
                raise ValueError('invalid_group_member_whitelist')
            users.add(member['user_id'])
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
