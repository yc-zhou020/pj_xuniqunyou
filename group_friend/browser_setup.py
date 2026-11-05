"""Open the dedicated browser for user-performed login; never sends messages."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from .runtime_support import InstanceLock
from .browser_session import BrowserSession, load_settings
from .group_memory_reader import GROUP_INFO


async def setup(path):
    settings = load_settings(path)
    session = BrowserSession(dict(settings, headless=False, manual_login=True))
    try:
        await session.start()
        print(json.dumps(dict(status='manual_login_browser_open', sent=False)), flush=True)
        print('请手动完成登录或安全验证。窗口会保持打开；看到就绪提示后，可继续核对目标会话，最后手动关闭窗口。', flush=True)
        last = None
        ready = False
        while True:
            if session.page.is_closed():
                return 0 if ready else 1
            try:
                state = await session.chat.login_state()
                if state != last:
                    print(json.dumps(dict(status='manual_login_state', state=state, sent=False)), flush=True)
                    last = state
                if state != 'ok':
                    ready = False
                if state == 'ok' and not ready:
                    if settings.get('owner_user_id'):
                        header = session.page.locator('div.RightPanelHeadertitle')
                        owner = await header.evaluate(GROUP_INFO) if await header.count() == 1 else None
                        if not owner or owner['owner_user_id'] != settings['owner_user_id']:
                            raise ValueError('manual_account_binding_required')
                    bound = []
                    for friend in settings['whitelist']:
                        if friend.get('is_self') or (friend.get('user_id') and friend.get('sec_uid')):
                            bound.append(friend)
                        else:
                            identity = await session.bind_visible_friend(friend['name'])
                            bound.append(dict(friend, **identity))
                    settings['whitelist'] = bound
                    destination = Path(path)
                    temporary = destination.with_suffix('.tmp')
                    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
                    temporary.replace(destination)
                    print(json.dumps(dict(status='manual_login_and_whitelist_ready',
                                          names=[f['name'] for f in bound], sent=False), ensure_ascii=False), flush=True)
                    print('当前登录和绑定就绪。请在浏览器中核对私聊与群聊是否能打开；完成后手动关闭浏览器窗口。', flush=True)
                    ready = True
            except Exception as exc:
                ready = False
                code = type(exc).__name__
                if last != code:
                    print(json.dumps(dict(status='waiting_for_manual_login_or_friend', reason=code, sent=False)), flush=True)
                    last = code
            await asyncio.sleep(5)
    finally:
        await session.close()


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Manual Firefox login; no model calls or sends')
    parser.add_argument('--config', default='browser.local.json')
    args = parser.parse_args()
    try:
        with InstanceLock('state/autonomous.lock'):
            return asyncio.run(setup(args.config))
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(json.dumps(dict(status='setup_stopped', reason=type(exc).__name__, sent=False)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
