"""uv run scripts/init_group_memory.py --group-title '高雅抖音'."""
import os
from pathlib import Path
import sys


if __name__ == '__main__':
    root = Path(__file__).resolve().parents[1]
    os.chdir(root)
    sys.path.insert(0, str(root))
    from group_friend.group_memory_initializer import main
    raise SystemExit(main())
