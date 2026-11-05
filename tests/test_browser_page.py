"""Real Firefox against a local fixture: never loads Douyin or uses login data."""
import os
from pathlib import Path
import unittest

from playwright.async_api import async_playwright

from group_friend.browser_engine import PageAdapter
from group_friend.browser_errors import BrowserUIError
from group_friend.browser_reference.douyin_chat import DouyinChat, FriendMismatch
from group_friend.browser_session import BrowserSession
from group_friend.conversation_snapshot import ConversationSnapshot


FRIEND = dict(name='Contact', douyin_id='account', sec_uid='participant')
HTML = '''<!doctype html><meta charset="utf-8">
<style>.messageMessageListlist{width:800px}.messageMessageBoxmessageBox{height:45px}
[data-e2e=msg-item-content]{display:inline-block;width:150px}.isFromMe{text-align:right}
[contenteditable]{width:600px;height:80px;border:1px solid}</style>
<div class="RightPanelHeadertitle">Contact</div>
<div class="messageMessageListlist">
<div class="messageMessageBoxmessageBox" data-message-id="1"><div data-e2e="msg-item-content">question</div></div>
<div class="messageMessageBoxmessageBox" data-message-id="2"><div data-e2e="msg-item-content">question</div></div>
</div><div data-e2e="msg-input"><div contenteditable="true"></div></div>
<button aria-label="发送">Send</button>
<script>
window.clicks=0;
document.querySelector('.RightPanelHeadertitle').__reactProps$fixture={
 conversation:{toParticipantSecUserId:'participant'},
 opponentUserProfile:{sec_uid:'participant',unique_id:'account',nickname:'Contact'}};
document.querySelector('button').onclick=()=>{
 window.clicks++;
 const editor=document.querySelector('[contenteditable]');
 const box=document.createElement('div');box.className='messageMessageBoxmessageBox isFromMe';
 box.dataset.messageId='sent-'+window.clicks;
 const content=document.createElement('div');content.dataset.e2e='msg-item-content';content.textContent=editor.innerText;
 box.append(content);document.querySelector('.messageMessageListlist').append(box);editor.innerText='';
};
</script>'''


class FirefoxPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_verification_overlay_blocks_actions_but_quoted_warning_does_not(self):
        from group_friend.browser_guard import require_no_verification
        from group_friend.browser_errors import ManualActionRequired
        await self.page.locator('[data-e2e="msg-item-content"]').first.evaluate(
            "el=>el.textContent='请完成下列验证后继续'")
        await require_no_verification(self.page)
        await self.page.evaluate('''()=>{
            const modal=document.createElement('div');modal.className='captcha_verify_container';
            modal.style='position:fixed;inset:0;background:white;z-index:9999';
            modal.textContent='请完成下列验证后继续';document.body.append(modal);
        }''')
        with self.assertRaises(ManualActionRequired):
            await self.adapter.select(FRIEND)
        self.assertEqual(await self.page.evaluate('window.clicks'),0)
        await self.page.locator('.captcha_verify_container').evaluate('el=>el.remove()')
        await require_no_verification(self.page)

    async def test_group_sender_binding_and_verified_single_click_reply(self):
        from group_friend.browser_errors import BrowserUIError
        from group_friend.browser_reference.douyin_chat import FriendMismatch
        members=[dict(name='Alice',user_id='1',sec_uid='sec-1'),dict(name='Bob',user_id='2',sec_uid='sec-2'),
                 dict(name='Self',user_id='99',sec_uid='sec-self',is_self=True)]
        group=dict(kind='group',name='Group',group_id='123',members=members)
        self.session.settings={'owner_user_id':'99'}
        await self.page.evaluate('''()=>{
          const h=document.querySelector('.RightPanelHeadertitle');h.innerText='Group(3)';
          const c={id:'123',type:2,participantCount:3,__internal_ctx:{option:{userId:'99'}},getParticipantList:()=>[]};
          h.__reactProps$fixture={conversation:c};
          document.querySelectorAll('.messageMessageBoxmessageBox').forEach((el,i)=>{
            el.__reactProps$group={message:{serverId:el.dataset.messageId,conversationId:'123',sender:String(i+1),isMyMessage:false}};
          });
          const button=document.querySelector('button'),previous=button.onclick;
          button.onclick=()=>{previous();const el=[...document.querySelectorAll('.messageMessageBoxmessageBox')].at(-1);
            el.__reactProps$group={message:{serverId:el.dataset.messageId,conversationId:'123',sender:'99',isMyMessage:true}};};
        }''')
        await self.adapter.select(group)
        messages=await self.adapter.read(group)
        self.assertEqual([m.sender_id for m in messages],['1','2'])
        self.assertEqual([m.direction for m in messages],['incoming','incoming'])
        snapshot=ConversationSnapshot(group,1000,tuple(messages),(0,))
        reply='group fixture answer'
        await self.adapter.preflight(group,snapshot,messages,reply)
        evidence=await self.adapter.prepare(group,snapshot,reply)
        await self.adapter.submit(group,snapshot,reply,evidence)
        current=await self.adapter.read(group)
        self.assertTrue(await self.adapter.confirm(group,reply,evidence,current))
        self.assertEqual(current[-1].sender_id,'99')
        self.assertEqual(current[-1].direction,'outgoing')
        with self.assertRaises(FriendMismatch):
            await self.session.verify_target(dict(group,group_id='124'))
        self.assertEqual(await self.page.evaluate('window.clicks'),1)
        await self.page.locator('.messageMessageBoxmessageBox').first.evaluate('el=>delete el.__reactProps$group')
        with self.assertRaises(BrowserUIError):
            await self.adapter.read(group)

    async def test_native_private_identity_requires_matching_uid_and_sec_uid(self):
        target=dict(name='Contact',user_id='1',sec_uid='participant')
        self.session.settings={'owner_user_id':'99'}
        await self.page.locator('.RightPanelHeadertitle').evaluate('''el=>{
          el.__reactProps$fixture={conversation:{id:'private-id',type:1,toParticipantSecUserId:'participant',
            toParticipantUserId:'1',__internal_ctx:{option:{userId:'99'}}}};
        }''')
        await self.session.verify_target(target)
        with self.assertRaises(FriendMismatch):
            await self.session.verify_target(dict(target,user_id='2'))
        self.session.settings['owner_user_id']='100'
        with self.assertRaises(FriendMismatch):
            await self.session.verify_target(target)

    async def test_group_capture_binds_all_members_and_never_sends(self):
        from group_friend.group_memory_reader import GroupMemoryReader
        await self.page.set_content('''<!doctype html><meta charset="utf-8">
        <style>.messageMessageListlist{height:180px;width:600px;overflow:scroll;display:flex;flex-direction:column-reverse}
        .messageMessageListlist>div{height:140px;flex-shrink:0}.overviewitemcontainer{width:160px;height:50px}</style>
        <div data-e2e="conversation-item">Group</div>
        <div class="RightPanelHeadertitle">Group(2)</div>
        <div class="RightPanelHeadertitleBarContainer">More</div>
        <div class="GroupParticipantsoverviewcontentArea" style="display:none">
          <div class="overviewitemcontainer"><span class="overviewitemname">Alice</span></div>
          <div class="overviewitemcontainer"><span class="overviewitemname">Bob</span></div>
        </div>
        <div class="messageMessageListlist"><div>Newest</div><div>Older</div><div>Oldest</div></div>
        <button aria-label="发送">Send</button>
        <script>
        const members=[{userId:'1',secUid:'sec-1'},{userId:'2',secUid:'sec-2'}];
        const c={id:'native-group',type:2,participantCount:2,getParticipantList:()=>members};
        document.querySelector('[data-e2e="conversation-item"]').__reactFiber$fixture={memoizedProps:{conversation:c}};
        document.querySelector('.RightPanelHeadertitle').__reactProps$fixture={conversation:c};
        document.querySelectorAll('.overviewitemcontainer').forEach((el,i)=>el.__reactFiber$fixture={memoizedProps:{participant:members[i]}});
        document.querySelector('.RightPanelHeadertitleBarContainer').onclick=()=>document.querySelector('.GroupParticipantsoverviewcontentArea').style.display='block';
        document.addEventListener('keydown',e=>{if(e.key==='Escape')document.querySelector('.GroupParticipantsoverviewcontentArea').style.display='none'});
        window.sends=0;document.querySelector('button').onclick=()=>window.sends++;
        </script>''')
        capture = await GroupMemoryReader(self.session).capture('Group', pages=2)
        self.assertEqual(capture.group_id, 'native-group')
        self.assertEqual(len(capture.members), 2)
        self.assertEqual(len(capture.member_images), 2)
        self.assertEqual(len(capture.chat_images), 2)
        self.assertTrue(all(data.startswith(b'\x89PNG') for data in capture.chat_images))
        self.assertEqual(await self.page.evaluate('window.sends'), 0)
        self.assertFalse(await self.page.locator('.GroupParticipantsoverviewcontentArea').is_visible())

    async def asyncSetUp(self):
        binaries = Path(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', '.playwright-browsers')).resolve()
        self.env = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(binaries)
        self.addCleanup(self.restore_browser_env)
        self.driver = await async_playwright().start()
        self.addAsyncCleanup(self.driver.stop)
        # Playwright resolves the executable for the current operating system.
        if not Path(self.driver.firefox.executable_path).is_file():
            if os.environ.get('REQUIRE_BROWSER_TESTS') == '1':
                self.fail('Project Firefox not installed; browser tests are required')
            self.skipTest('Project Firefox not installed')
        self.browser = await self.driver.firefox.launch(headless=True)
        self.addAsyncCleanup(self.browser.close)
        self.page = await self.browser.new_page(viewport={'width':1200,'height':800})
        await self.page.set_content(HTML)
        self.session = BrowserSession({})
        self.session.page = self.page
        self.session.chat = DouyinChat(self.page, timeout_ms=1000)
        self.adapter = PageAdapter(self.session)

    def restore_browser_env(self):
        if self.env is None:
            os.environ.pop('PLAYWRIGHT_BROWSERS_PATH', None)
        else:
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = self.env

    async def test_reader_and_single_click_send_without_a_visible_window(self):
        await self.adapter.select(FRIEND)
        messages = await self.adapter.read(FRIEND)
        self.assertEqual([m.text for m in messages], ['question','question'])
        self.assertEqual([m.message_id for m in messages], ['1','2'])
        self.assertTrue(all(m.direction=='incoming' for m in messages))
        snapshot = ConversationSnapshot(FRIEND, 1, tuple(messages), (1,))
        reply = 'local fixture reply'
        await self.adapter.preflight(FRIEND, snapshot, messages, reply)
        evidence = await self.adapter.prepare(FRIEND, snapshot, reply)
        await self.adapter.submit(FRIEND, snapshot, reply, evidence)
        current = await self.adapter.read(FRIEND)
        self.assertTrue(await self.adapter.confirm(FRIEND, reply, evidence, current))
        self.assertEqual(current[-1].direction, 'outgoing')
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)

    async def test_existing_draft_and_wrong_identity_block_send(self):
        await self.page.locator('[contenteditable]').fill('manual draft')
        messages = await self.adapter.read(FRIEND)
        snapshot = ConversationSnapshot(FRIEND, 1, tuple(messages), (1,))
        with self.assertRaises(BrowserUIError):
            await self.adapter.preflight(FRIEND, snapshot, messages, 'reply')
        with self.assertRaises(FriendMismatch):
            await self.adapter.read(dict(FRIEND, sec_uid='wrong-participant'))
        self.assertEqual(await self.adapter.draft(), 'manual draft')
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)

    async def test_firefox_composition_fill_duplicates_but_keyboard_path_sends_once(self):
        # Emulate the production editor-kit composition handler observed in Firefox.
        await self.page.locator('[contenteditable]').evaluate('''el=>el.addEventListener('input',e=>{
          if(e.inputType==='insertCompositionText' && e.data?.length>1){
            el.textContent='';for(let i=0;i<2;i++){const line=document.createElement('div');line.textContent=e.data;el.append(line);}
          }
        })''')
        reply = '本地输入回归测试'
        await self.page.locator('[contenteditable]').fill(reply)
        self.assertEqual(await self.adapter.draft(), reply+'\n'+reply)
        await self.page.locator('[contenteditable]').evaluate("el=>el.textContent=''")
        messages = await self.adapter.read(FRIEND)
        snapshot = ConversationSnapshot(FRIEND, 1, tuple(messages), (1,))
        evidence = await self.adapter.prepare(FRIEND, snapshot, reply)
        self.assertEqual(await self.adapter.draft(), reply)
        self.assertEqual(await self.page.evaluate('window.clicks'), 0)
        await self.adapter.submit(FRIEND, snapshot, reply, evidence)
        self.assertTrue(await self.adapter.confirm(FRIEND, reply, evidence, await self.adapter.read(FRIEND)))
        self.assertEqual(await self.page.evaluate('window.clicks'), 1)
