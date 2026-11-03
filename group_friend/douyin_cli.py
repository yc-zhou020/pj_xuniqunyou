"""Experimental read-only Windows preview and explicit historical replay."""
import argparse
import json
import hashlib
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict
from pathlib import Path

from .config import Config, load_env
from .douyin import (Binding, ObservationError, SnapshotTracker, WindowsObserver,
                     capture, parse_window, select_window)
from .llm import DeepSeek, MockModel, ModelError
from .memory import MEMORY_DATA_RULE, MemoryStorageError, augment_context, open_memory


def emit(**data):
    print(json.dumps(data, ensure_ascii=False), flush=True)


def read_source(args):
    if args.snapshot:
        report = json.loads(Path(args.snapshot).read_text(encoding="utf-8-sig"))
        window = select_window(report, args.window_handle)
        return parse_window(window, expected_title=args.window_title, expected_heading=args.group_heading)
    return WindowsObserver(Binding.load(args.binding)).read()


def replay(messages, index, model, config, *, memory=None, memory_space=None, memory_source=None, memory_trace=None):
    """Explicit manual replay, never the live Engine and never a transport."""
    if not 0 <= index < len(messages):
        raise ObservationError("message_index_out_of_range")
    if Path(config.pause_path).exists():
        raise ObservationError("paused")
    text = messages[index].text[:config.context_chars]
    persona = Path(config.persona_path).read_text(encoding="utf-8")
    persona += ("\nThis is a manually selected historical text replay, not a new message. "
                "No tools or actions are available. You have not viewed linked media.")
    context = [{"role": "user", "content": text}]
    if memory is not None and memory_space is not None and config.memory_enabled:
        context, ids = augment_context(memory, memory_space, text, context, config, exclude_source=memory_source)
        memory.add(memory_space, messages[index].text[:16000], source_key=memory_source)
        if ids:
            persona += MEMORY_DATA_RULE
        if memory_trace is not None:
            memory_trace.update(space=memory_space, recalled_memory_ids=ids)
    reply = model.reply(persona, context)
    if Path(config.pause_path).exists():
        raise ObservationError("paused_after_generation")
    if not isinstance(reply, str) or not reply.strip():
        raise ObservationError("empty_model_reply")
    reply = reply.strip()
    if not reply.startswith("[AI]"):
        reply = "[AI] " + reply
    return reply[:config.max_reply_chars]


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Douyin read-only preview; no sending capability")
    sub = parser.add_subparsers(dest="command", required=True)
    bind = sub.add_parser("bind", help="Bind a manually selected group window for this client process")
    bind.add_argument("--window-handle", type=int, required=True)
    bind.add_argument("--window-title", required=True)
    bind.add_argument("--group-heading", required=True)
    bind.add_argument("--binding", default="state/douyin-session.json")
    for command in ("inspect", "watch", "replay"):
        cmd = sub.add_parser(command)
        cmd.add_argument("--binding", default="state/douyin-session.json")
        cmd.add_argument("--show-text", action="store_true", help="Print visible text/names locally")
        if command != "watch":
            cmd.add_argument("--snapshot", help="Read a saved MSAA snapshot instead of the desktop")
            cmd.add_argument("--window-handle", type=int)
            cmd.add_argument("--window-title")
            cmd.add_argument("--group-heading")
        if command == "watch":
            cmd.add_argument("--interval", type=float, default=5)
            cmd.add_argument("--captures", type=int, default=0, help="0 runs until Ctrl+C")
        if command == "replay":
            cmd.add_argument("--index", type=int, required=True, help="Index from inspect; use the same snapshot for reproducibility")
            cmd.add_argument("--mock", action="store_true")
            cmd.add_argument("--config", default="config.example.json")
    args = parser.parse_args(argv)
    memory = None
    try:
        if args.command == "bind":
            report = capture()
            window = select_window(report, args.window_handle)
            messages = parse_window(window, expected_title=args.window_title, expected_heading=args.group_heading)
            if not window.get("process_started_at"):
                raise ObservationError("process_start_time_missing")
            binding = Binding(window["process_id"], args.window_handle, window["process_started_at"],
                              args.window_title, args.group_heading, str(uuid.uuid4()))
            binding.save(args.binding)
            emit(status="bound_read_only", text_messages=len(messages), binding=args.binding)
        elif args.command == "inspect":
            messages = read_source(args)
            emit(status="observed", text_messages=len(messages), platform_ids_available=False)
            for index, message in enumerate(messages):
                item = dict(index=index, characters=len(message.text), sender_label_available=bool(message.sender_name),
                            sender_inherited=message.sender_inherited, is_self=message.is_self)
                if args.show_text:
                    item.update(asdict(message))
                emit(**item)
        elif args.command == "replay":
            # Requiring a snapshot prevents an index silently selecting a different
            # row between inspect and replay while the desktop is updating.
            if not args.snapshot:
                raise ObservationError("replay_requires_saved_snapshot")
            messages = read_source(args)
            config = Config.load(args.config)
            load_env()
            model = MockModel() if args.mock else DeepSeek(config)
            memory = open_memory(config) if config.memory_enabled else None
            binding = Binding.load(args.binding) if Path(args.binding).exists() else None
            # A saved snapshot must match the linked target before its memory can be used.
            scope = (memory.space_for_binding(binding) if memory is not None and binding is not None
                     and args.window_title == binding.window_title and args.group_heading == binding.group_heading else None)
            source = "snapshot:" + hashlib.sha256(Path(args.snapshot).read_bytes()).hexdigest() + ":" + str(args.window_handle) + ":" + str(args.index)
            trace = {}
            response = replay(messages, args.index, model, config, memory=memory, memory_space=scope,
                              memory_source=source, memory_trace=trace)
            emit(status="manual_historical_replay", index=args.index, reply=response, sent=False, memory=trace)
        else:
            if not 5 <= args.interval <= 3600 or args.captures < 0:
                raise ObservationError("invalid_watch_interval_or_count")
            observer = WindowsObserver(Binding.load(args.binding))
            tracker = SnapshotTracker()  # A new local observation session on each start.
            count = 0
            while True:
                if Path("state/PAUSE").exists():
                    emit(status="paused_observer_stopped")
                    break
                messages = observer.read()
                events = tracker.update(messages)
                emit(status="baseline" if count == 0 else "observed", text_messages=len(messages), candidates=len(events))
                for observation_id, message in events:
                    item = dict(status="candidate_only", observation_id=observation_id,
                                platform_message_id=None, sender_id=None, is_self=None,
                                sender_inherited=message.sender_inherited, sent=False)
                    if args.show_text:
                        item.update(text=message.text, sender_name=message.sender_name)
                    emit(**item)
                count += 1
                if args.captures and count >= args.captures:
                    break
                # Poll pause during the interval; no sleeping past a full minute.
                deadline = time.monotonic() + args.interval
                while time.monotonic() < deadline and not Path("state/PAUSE").exists():
                    time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        emit(status="stopped")
    except (ObservationError, ModelError, MemoryStorageError) as exc:
        emit(status="stopped", reason=str(exc))
        return 1
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        emit(status="stopped", reason="invalid_config_or_snapshot")
        return 1
    finally:
        if memory is not None:
            memory.close()
    return 0
