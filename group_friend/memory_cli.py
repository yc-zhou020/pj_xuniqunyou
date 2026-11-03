"""Local storage administration. No model requests or Douyin sends."""
import argparse
import sqlite3
import sys
from pathlib import Path

from .config import Config
from .douyin import Binding
from .douyin_cli import emit
from .memory import MemoryStorageError, open_memory


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local long-term memory spaces and capacity budget")
    parser.add_argument("--config", default="config.example.json")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--capacity-mb", type=int)
    init.add_argument("--retention-days", type=int)
    sub.add_parser("stats")
    sub.add_parser("spaces")
    sub.add_parser("prune")
    sub.add_parser("compact")
    resize = sub.add_parser("resize")
    resize.add_argument("--capacity-mb", type=int, required=True)
    retention = sub.add_parser("retention")
    retention.add_argument("--days", type=int, required=True)
    create = sub.add_parser("create-space")
    create.add_argument("--label", required=True)
    for name in ("link", "add", "list", "search", "show", "forget", "clear", "pin", "unpin"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--space", required=True)
        if name == "link":
            cmd.add_argument("--binding", default="state/douyin-session.json")
        if name == "add":
            source = cmd.add_mutually_exclusive_group(required=True)
            source.add_argument("--text")
            source.add_argument("--text-file")
            cmd.add_argument("--pin", action="store_true")
        if name in ("list", "search"):
            cmd.add_argument("--limit", type=int, default=20 if name == "list" else 6)
            cmd.add_argument("--show-text", action="store_true")
        if name == "search":
            cmd.add_argument("--query", required=True)
        if name in ("show", "forget", "pin", "unpin"):
            cmd.add_argument("--id", required=True)
    args = parser.parse_args(argv)
    try:
        config = Config.load(args.config)
        with open_memory(config) as memory:
            if args.command == "init":
                if args.capacity_mb is not None:
                    if args.capacity_mb < 1:
                        raise MemoryStorageError("capacity_mb_must_be_positive")
                    memory.resize(args.capacity_mb * 1024 * 1024)
                if args.retention_days is not None:
                    memory.set_retention(args.retention_days)
                emit(**memory.stats())
            elif args.command == "stats":
                emit(**memory.stats())
            elif args.command == "spaces":
                emit(spaces=memory.spaces())
            elif args.command == "create-space":
                emit(space=memory.create_space(args.label), label=args.label)
            elif args.command == "link":
                memory.link_binding(Binding.load(args.binding), args.space)
                emit(status="linked", space=args.space, binding=args.binding)
            elif args.command == "add":
                text = Path(args.text_file).read_text(encoding="utf-8-sig") if args.text_file else args.text
                entry_id = memory.add(args.space, text, kind="note", pinned=args.pin)
                emit(status="stored", id=entry_id, space=args.space)
            elif args.command in ("list", "search"):
                if not 1 <= args.limit <= 100:
                    raise MemoryStorageError("limit_must_be_1_to_100")
                rows = (memory.list(args.space, args.limit) if args.command == "list"
                        else memory.search(args.space, args.query, limit=args.limit))
                if not args.show_text:
                    rows = [{key: row[key] for key in ("id", "kind", "created", "pinned", "size_bytes")} for row in rows]
                emit(entries=rows)
            elif args.command == "show":
                row = memory.db.execute("SELECT * FROM entries WHERE space_id=? AND id=?", (args.space, args.id)).fetchone()
                if row is None or row["expires"] <= memory.clock():
                    raise MemoryStorageError("memory_entry_not_found")
                emit(entry=dict(row))
            elif args.command in ("forget", "clear"):
                emit(removed=memory.forget(args.space, args.id if args.command == "forget" else None))
            elif args.command in ("pin", "unpin"):
                memory.pin(args.space, args.id, args.command == "pin")
                emit(status=args.command)
            elif args.command == "prune":
                emit(expired_removed=memory.prune())
            elif args.command == "resize":
                if args.capacity_mb < 1:
                    raise MemoryStorageError("capacity_mb_must_be_positive")
                memory.resize(args.capacity_mb * 1024 * 1024)
                emit(**memory.stats())
            elif args.command == "retention":
                memory.set_retention(args.days)
                emit(**memory.stats())
            else:
                memory.compact()
                emit(**memory.stats())
    except MemoryStorageError as exc:
        emit(status="stopped", reason=str(exc))
        return 1
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error):
        emit(status="stopped", reason="memory_storage_or_config_error")
        return 1
    return 0
