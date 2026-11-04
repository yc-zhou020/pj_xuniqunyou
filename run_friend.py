"""Run the whitelist private-chat friend without Codex or an IDE session."""
import os
import argparse
import json
from pathlib import Path
import sys


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--config')
    args, _ = parser.parse_known_args(argv)
    path = args.config or ('browser.local.json' if Path('browser.local.json').exists() else 'autonomous.local.json')
    if args.config is None:
        argv = ['--config', path, *argv]
    try:
        browser = json.loads(Path(path).read_text(encoding='utf-8-sig')).get('backend') == 'browser'
    except (OSError, ValueError, AttributeError):
        browser = False
    if browser:
        from group_friend.browser_runtime import main as run
    else:
        from group_friend.autonomous import main as run
    return run(argv)


if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parent)
    raise SystemExit(main())
