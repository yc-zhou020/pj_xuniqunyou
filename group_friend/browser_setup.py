"""Open the dedicated browser for user-performed login; never sends messages."""
import argparse
import asyncio
import json
from pathlib import Path
import sys

from .runtime_support import InstanceLock
from .browser_session import BrowserSession, load_settings


async def setup(path):
    settings = load_settings(path)
    session = BrowserSession(dict(settings, headless=False, manual_login=True))
    try:
        await session.start()
        print(json.dumps(dict(status='manual_login_browser_open', sent=False)), flush=True)
        last = None
        while True:
            if session.page.is_closed():
                return 1
            try:
                state = await session.chat.login_state()
                if state != last:
                    print(json.dumps(dict(status='manual_login_state', state=state, sent=False)), flush=True)
                    last = state
                if state == 'ok':
                    bound = []
                    for friend in settings['whitelist']:
                        bound.append(await session.bind_visible_friend(friend['name']))
                    settings['whitelist'] = bound
                    destination = Path(path)
                    temporary = destination.with_suffix('.tmp')
                    temporary.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding='utf-8')
                    temporary.replace(destination)
                    print(json.dumps(dict(status='manual_login_and_whitelist_ready',
                                          names=[f['name'] for f in bound], sent=False), ensure_ascii=False), flush=True)
                    return 0
            except Exception as exc:
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
