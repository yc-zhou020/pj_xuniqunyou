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
    async def asyncSetUp(self):
        binaries = Path('.playwright-browsers').resolve()
        if not list(binaries.glob('firefox-*/firefox/firefox.exe')):
            self.skipTest('Project Firefox not installed')
        self.env = os.environ.get('PLAYWRIGHT_BROWSERS_PATH')
        os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(binaries)
        self.driver = await async_playwright().start()
        self.browser = await self.driver.firefox.launch(headless=True)
        self.page = await self.browser.new_page(viewport={'width':1200,'height':800})
        await self.page.set_content(HTML)
        self.session = BrowserSession({})
        self.session.page = self.page
        self.session.chat = DouyinChat(self.page, timeout_ms=1000)
        self.adapter = PageAdapter(self.session)

    async def asyncTearDown(self):
        await self.browser.close()
        await self.driver.stop()
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
        reply = '[AI] local fixture reply'
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
            await self.adapter.preflight(FRIEND, snapshot, messages, '[AI] reply')
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
        reply = '[AI] 本地输入回归测试'
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
