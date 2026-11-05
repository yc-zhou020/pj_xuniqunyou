"""Run the Firefox whitelist private-chat friend without Codex."""
import os
from pathlib import Path


def main(argv=None):
    from group_friend.browser_runtime import main as run
    return run(argv)


if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parent)
    raise SystemExit(main())
