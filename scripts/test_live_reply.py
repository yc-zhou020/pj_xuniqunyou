"""Opt-in real replies: uv run scripts/test_live_reply.py."""
import os
from pathlib import Path
import sys


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    from group_friend.live_test import main
    raise SystemExit(main())
