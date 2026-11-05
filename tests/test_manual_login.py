import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from group_friend import browser_setup


class ManualLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_login_stays_open_until_user_closes_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'browser.json'
            settings=dict(whitelist=[dict(name='Contact',user_id='1',sec_uid='sec-1')])
            page=Mock()
            page.is_closed.side_effect=[False,False,True]
            session=Mock(page=page,start=AsyncMock(),close=AsyncMock())
            session.chat.login_state=AsyncMock(return_value='ok')
            with patch.object(browser_setup,'load_settings',return_value=settings), \
                 patch.object(browser_setup,'BrowserSession',return_value=session), \
                 patch.object(browser_setup.asyncio,'sleep',new_callable=AsyncMock) as wait, \
                 patch('builtins.print'):
                result=await browser_setup.setup(path)
            self.assertEqual(result,0)
            self.assertEqual(session.chat.login_state.await_count,2)
            self.assertEqual(wait.await_count,2)
            session.close.assert_awaited_once()

    async def test_unresolved_verification_is_not_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            settings=dict(whitelist=[dict(name='Contact',user_id='1',sec_uid='sec-1')])
            page=Mock()
            page.is_closed.side_effect=[False,True]
            session=Mock(page=page,start=AsyncMock(),close=AsyncMock())
            session.chat.login_state=AsyncMock(return_value='risk')
            with patch.object(browser_setup,'load_settings',return_value=settings), \
                 patch.object(browser_setup,'BrowserSession',return_value=session), \
                 patch.object(browser_setup.asyncio,'sleep',new_callable=AsyncMock), \
                 patch('builtins.print') as output:
                result=await browser_setup.setup(Path(tmp)/'browser.json')
            self.assertEqual(result,1)
            self.assertNotIn('manual_login_and_whitelist_ready',str(output.call_args_list))
