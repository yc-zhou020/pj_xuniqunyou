"""Run the whitelist private-chat friend without Codex or an IDE session."""
import os
from pathlib import Path

from group_friend.autonomous import main


if __name__ == '__main__':
    os.chdir(Path(__file__).resolve().parent)
    raise SystemExit(main())
