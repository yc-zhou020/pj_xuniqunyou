"""Process lock, rotating logs and reply validation for the browser runner."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys


class InstanceLock:
    def __init__(self, path):
        self.path = Path(path)
        self.file = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        try:
            if self.path.stat().st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise ValueError('runtime_already_running') from None
        return self

    def __exit__(self, *args):
        if self.file:
            self.file.close()


def make_logger(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('group_friend.autonomous')
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    for handler in (logging.StreamHandler(sys.stdout), RotatingFileHandler(path, maxBytes=2_000_000, backupCount=3, encoding='utf-8')):
        handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
        logger.addHandler(handler)
    return logger


class ReplyError(ValueError):
    pass


def validate_reply(text, limit=300):
    if not isinstance(text, str) or not text.strip():
        raise ReplyError("empty_reply")
    text = text.strip()
    if not text.startswith("[AI]"):
        text = "[AI] " + text
    if len(text) > limit or any(ord(c) < 32 or 0x7f <= ord(c) < 0xa0 for c in text):
        raise ReplyError("reply_must_be_single_line_within_limit")
    return text


