"""Read-only group/member binding and pixel capture from the logged-in Douyin UI."""
import asyncio
from dataclasses import dataclass
import re

from .browser_errors import BrowserUIError
from .browser_reference.page_selectors import MESSAGE_LIST


GROUP_INFO = r'''el => {
 const candidates=[], fk=Object.keys(el).find(k=>k.startsWith('__reactFiber'));
 const pk=Object.keys(el).find(k=>k.startsWith('__reactProps'));
 if(pk)candidates.push(el[pk]);let f=fk&&el[fk];
 for(let n=0;n<8&&f;n++,f=f.return)candidates.push(f.memoizedProps);
 const found=new Map();
 for(const p of candidates){const c=p?.conversation||p?.curConversation;if(!c)continue;
  const id=String(c.id||'');if(!id)continue;
  const list=typeof c.getParticipantList==='function'?c.getParticipantList():[];
  found.set(id,{id,type:Number(c.type),count:Number(c.participantCount),
   owner_user_id:String(c.__internal_ctx?.option?.userId||''),
   members:Array.isArray(list)?list.map(x=>({user_id:String(x.userId||''),sec_uid:String(x.secUid||'')})):[]});
 }
 return found.size===1?[...found.values()][0]:null;
}'''

MEMBER_INFO = r'''el => {
 const fk=Object.keys(el).find(k=>k.startsWith('__reactFiber'));let f=fk&&el[fk];
 for(let n=0;n<5&&f;n++,f=f.return){const p=f.memoizedProps?.participant;
  if(p)return {user_id:String(p.userId||''),sec_uid:String(p.secUid||''),
   name:(el.querySelector('[class*="overviewitemname"]')?.innerText||'').trim()};
 }return null;
}'''


@dataclass
class GroupCapture:
    group_id: str
    title: str
    members: list
    member_images: dict
    chat_images: list


class GroupMemoryReader:
    def __init__(self, session):
        self.session = session
        self.group_id = None
        self.title = None

    async def verify(self):
        # Use the narrow title class; titleContainer also matches the substring.
        header = self.session.page.locator('div.RightPanelHeadertitle')
        if await header.count() != 1 or not await header.is_visible():
            raise BrowserUIError('group_header_unavailable')
        title = ' '.join((await header.inner_text()).split())
        if not re.fullmatch(re.escape(self.title) + r'(?:\(\d+\)|（\d+）)?', title):
            raise BrowserUIError('group_title_changed')
        binding = await header.evaluate(GROUP_INFO)
        if not binding or binding['id'] != self.group_id or binding['type'] != 2:
            raise BrowserUIError('group_identity_changed')

    async def capture(self, title, *, group_id=None, pages=6):
        if not title.strip() or not 1 <= pages <= 10:
            raise ValueError('invalid_group_capture_arguments')
        self.title = title
        rows = await self.session.chat._rows_by_title(title, exact=True)
        selected = []
        for row in rows:
            binding = await row.evaluate(GROUP_INFO)
            if binding and binding['type'] == 2 and (not group_id or binding['id'] == group_id):
                selected.append((row, binding))
        if len(selected) != 1:
            raise BrowserUIError('group_not_unique_or_not_visible')
        row, binding = selected[0]
        self.group_id = binding['id']
        await self.session.chat._open_row(row)
        # Rendering may initially retain the previous private-chat header.
        for attempt in range(20):
            try:
                await self.verify()
                break
            except BrowserUIError:
                if attempt == 19:
                    raise
                await asyncio.sleep(.25)
        await self.session.page.locator('[class*="RightPanelHeadertitleBarContainer"]').click()
        cards = self.session.page.locator('[class*="GroupParticipantsoverviewcontentArea"] > [class*="overviewitemcontainer"]')
        await cards.first.wait_for(state='visible', timeout=8000)
        # Opening the drawer populates the native participant cache. The row's
        # pre-open data may still report an empty or stale roster.
        for attempt in range(20):
            binding = await self.session.page.locator('div.RightPanelHeadertitle').evaluate(GROUP_INFO)
            if not binding or binding['id'] != self.group_id or binding['type'] != 2:
                raise BrowserUIError('group_identity_changed')
            if (binding['count'] and len(binding['members']) == binding['count']
                    and await cards.count() == binding['count']):
                break
            if attempt == 19:
                raise BrowserUIError(f"group_roster_incomplete: expected={binding['count']}, native={len(binding['members'])}, cards={await cards.count()}")
            await asyncio.sleep(.25)
        members, member_images = [], {}
        native = {item['user_id']: item['sec_uid'] for item in binding['members']}
        # Nicknames finish loading asynchronously and may reorder cards. Bind
        # each DOM card by native participant ID rather than a shifting index.
        stamp = ('elements=>{const read=(' + MEMBER_INFO + ');return elements.map(el=>{'
                 'const p=read(el);if(p)el.setAttribute("data-chen-memory-member",p.user_id);return p;});}')
        roster = await cards.evaluate_all(stamp)
        if any(not item for item in roster):
            raise BrowserUIError('group_member_identity_unconfirmed')
        for info in sorted(roster, key=lambda item: item['user_id']):
            if (not info or not info['name'] or not info['user_id'] or not info['sec_uid']
                    or not info['user_id'].isdigit() or native.get(info['user_id']) != info['sec_uid']):
                raise BrowserUIError('group_member_identity_unconfirmed')
            if any(m['user_id'] == info['user_id'] for m in members):
                raise BrowserUIError('duplicate_group_member_identity')
            await cards.evaluate_all(stamp)
            card = self.session.page.locator('[data-chen-memory-member="' + info['user_id'] + '"]')
            if await card.count() != 1:
                raise BrowserUIError('group_member_identity_unconfirmed')
            current = await card.evaluate(MEMBER_INFO)
            if current['user_id'] != info['user_id'] or current['sec_uid'] != info['sec_uid']:
                raise BrowserUIError('group_member_identity_changed')
            info = current
            await self.verify()
            member_images[info['user_id']] = await card.screenshot(type='png')
            if await card.evaluate(MEMBER_INFO) != info:
                raise BrowserUIError('group_member_identity_changed')
            members.append(info)
        if len(members) != binding['count'] or len(native) != binding['count']:
            raise BrowserUIError(f"group_roster_incomplete: expected={binding['count']}, native={len(native)}, captured={len(members)}")
        # Escape dismisses the drawer; the ellipsis opens it again rather than
        # toggling it closed on the current Douyin UI.
        await self.session.page.locator('[class*="RightPanelHeadertitleBarContainer"]').click()
        await self.session.page.keyboard.press('Escape')
        try:
            await cards.first.wait_for(state='hidden', timeout=3000)
        except Exception:
            raise BrowserUIError('group_details_not_dismissed') from None
        chat = None
        for selector in MESSAGE_LIST:
            candidates = self.session.page.locator(selector)
            if await candidates.count() == 1 and await candidates.is_visible():
                chat = candidates
                break
        if chat is None:
            raise BrowserUIError('group_message_list_unavailable')
        images = []
        for _ in range(pages):
            await self.verify()
            image = await chat.screenshot(type='png')
            if images and image == images[-1]:
                break
            images.append(image)
            # Scroll the message list itself, not the conversation sidebar.
            moved = await chat.evaluate('''el=>{
              const before=el.scrollTop;el.scrollTop=before-el.clientHeight*.8;
              return {before,after:el.scrollTop};
            }''')
            if moved['before'] == moved['after']:
                break
            await asyncio.sleep(.6)
        await self.verify()
        final_binding = await self.session.page.locator('div.RightPanelHeadertitle').evaluate(GROUP_INFO)
        final_members = {item['user_id']: item['sec_uid'] for item in final_binding['members']}
        if final_binding['count'] != len(members) or final_members != native:
            raise BrowserUIError('group_members_changed_during_capture')
        return GroupCapture(self.group_id, title, members, member_images, images)
