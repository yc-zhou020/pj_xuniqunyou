"""Generate -> review/edit -> approve exact version -> explicitly send once."""
import argparse
import json
import hashlib
import sqlite3
import sys
from pathlib import Path

from .config import Config, load_env
from .douyin import Binding, ObservationError, parse_window, select_window
from .douyin_cli import emit, replay
from .llm import DeepSeek, MockModel, ModelError
from .review import ReviewError, ReviewQueue, binding_key, send_reviewed
from .review_transport import WindowsReviewedTransport
from .memory import MemoryStorageError, open_memory


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Local reply review queue; approval alone never sends")
    parser.add_argument("--queue", default="state/replies.sqlite3")
    parser.add_argument("--binding", default="state/douyin-session.json")
    parser.add_argument("--config", default="config.example.json")
    sub = parser.add_subparsers(dest="command", required=True)
    generate = sub.add_parser("generate", help="Generate a pending reply from one manually selected snapshot row")
    generate.add_argument("--snapshot", required=True)
    generate.add_argument("--index", type=int, required=True)
    generate.add_argument("--snapshot-window-handle", type=int)
    generate.add_argument("--mock", action="store_true")
    sub.add_parser("list")
    for name in ("show", "edit", "discard", "approve", "check", "send", "resolve"):
        command = sub.add_parser(name)
        command.add_argument("id")
        if name in ("edit", "discard", "approve", "send"):
            command.add_argument("--revision", type=int, required=True)
        if name == "edit":
            edit = command.add_mutually_exclusive_group(required=True)
            edit.add_argument("--text")
            edit.add_argument("--text-file")
        elif name == "approve":
            command.add_argument("--target", required=True, help="Exact group heading shown by review show")
        elif name == "resolve":
            command.add_argument("--outcome", choices=("visible_sent", "not_sent"), required=True)
            command.add_argument("--note", required=True, help="Record your manual check; this does not retry sending")
    purge = sub.add_parser("purge")
    purge.add_argument("--days", type=int, default=7)
    args = parser.parse_args(argv)
    queue = None
    memory = None
    try:
        queue = ReviewQueue(args.queue)
        if args.command == "generate":
            binding = Binding.load(args.binding)
            report = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
            handle = args.snapshot_window_handle or binding.window_handle
            window = select_window(report, handle)
            messages = parse_window(window, expected_title=binding.window_title, expected_heading=binding.group_heading)
            config = Config.load(args.config)
            load_env()
            model = MockModel() if args.mock else DeepSeek(config)
            memory = open_memory(config) if config.memory_enabled else None
            scope = memory.space_for_binding(binding) if memory is not None else None
            source = "snapshot:" + hashlib.sha256(Path(args.snapshot).read_bytes()).hexdigest() + ":" + str(handle) + ":" + str(args.index)
            trace = {}
            reply = replay(messages, args.index, model, config, memory=memory, memory_space=scope,
                           memory_source=source, memory_trace=trace)
            # Sending is single-line; preserve words while flattening model paragraphs.
            reply = " ".join(reply.splitlines())
            item_id = queue.enqueue(binding, messages[args.index].text, reply)
            emit(status="pending_review", id=item_id, revision=1, target=binding.group_heading,
                 reply=reply, sent=False, memory=trace)
        elif args.command == "list":
            emit(items=queue.list())
        elif args.command == "show":
            item = queue.get(args.id)
            emit(id=item["id"], state=item["state"], revision=item["revision"],
                 target=item["binding"]["group_heading"], source=item["source"], reply=item["reply"],
                 outcome=item["outcome"], platform_ids_available=False)
        elif args.command == "edit":
            text = Path(args.text_file).read_text(encoding="utf-8-sig") if args.text_file else args.text
            queue.edit(args.id, args.revision, text)
            emit(status="pending_review", revision=args.revision + 1, approval_revoked=True, sent=False)
        elif args.command == "discard":
            queue.discard(args.id, args.revision)
            emit(status="discarded", content_cleared=True, sent=False)
        elif args.command == "approve":
            queue.approve(args.id, args.revision, target_heading=args.target, binding=Binding.load(args.binding))
            emit(status="approved", revision=args.revision, sent=False, approval_expires_in_seconds=600)
        elif args.command == "check":
            binding = Binding.load(args.binding)
            item = queue.get(args.id)
            if item["binding_key"] != binding_key(binding):
                raise ReviewError("session_changed_recreate_reply")
            transport = WindowsReviewedTransport()
            transport.preflight(binding, item["source"])
            outcome = transport.check(binding, item["source"], item["reply"], Config.load(args.config).pause_path)
            emit(status="preflight_ready" if outcome == "ready" else "preflight_not_ready", sent=False)
        elif args.command == "send":
            config = Config.load(args.config)
            binding = Binding.load(args.binding)
            outcome = send_reviewed(queue, args.id, args.revision, binding,
                                    WindowsReviewedTransport(), config.pause_path, config)
            memory_status = "not_stored"
            if outcome == "visible_sent" and config.memory_enabled:
                try:
                    memory = open_memory(config)
                    scope = memory.space_for_binding(binding)
                    if scope is not None:
                        memory.add(scope, queue.get(args.id)["reply"], kind="assistant", source_key="review:sent:" + args.id)
                        memory_status = "stored"
                except (MemoryStorageError, sqlite3.Error, OSError):
                    memory_status = "storage_unavailable"
            emit(status=outcome, retry_allowed=False, memory=memory_status)
        elif args.command == "resolve":
            queue.resolve(args.id, args.outcome, args.note)
            emit(status=args.outcome, manual_resolution=True, retry_allowed=False)
        else:
            emit(removed=queue.purge(args.days))
    except KeyboardInterrupt:
        emit(status="stopped", note="If sending had started, inspect queue before any further send.")
        return 1
    except (ReviewError, ObservationError, ModelError, MemoryStorageError) as exc:
        emit(status="stopped", reason=str(exc))
        return 1
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        emit(status="stopped", reason="invalid_config_or_local_data")
        return 1
    finally:
        if queue is not None:
            queue.close()
        if memory is not None:
            memory.close()
    return 0
