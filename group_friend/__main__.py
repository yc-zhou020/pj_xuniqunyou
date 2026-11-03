import argparse
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

from .config import Config, load_env
from .core import Engine, Message, Store
from .llm import DeepSeek, MockModel
from .memory import MemoryStorageError, open_memory


class UnverifiedTransport:
    def verify_target(self, conversation_id):
        return False

    def send(self, conversation_id, text):
        raise RuntimeError("Douyin transport has not been verified")


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "credentials":
        from .credentials_cli import main as credentials_main
        return credentials_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "memory":
        from .memory_cli import main as memory_main
        return memory_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "review":
        from .review_cli import main as review_main
        return review_main(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "douyin":
        from .douyin_cli import main as douyin_main
        return douyin_main(sys.argv[2:])
    parser = argparse.ArgumentParser(description="DeepSeek group friend: local dry-run console")
    parser.add_argument("--config", default="config.example.json")
    parser.add_argument("--mock", action="store_true", help="Use local fixed response; no API request")
    args = parser.parse_args()
    store = None
    memory = None
    try:
        load_env()
        config = Config.load(args.config)
        if not config.dry_run:
            raise ValueError("Console currently supports dry_run=true only; Douyin transport is unverified")
        model = MockModel() if args.mock else DeepSeek(config)
        store = Store(config.state_path)
        memory = open_memory(config) if config.memory_enabled else None
        engine = Engine(config, store, model, UnverifiedTransport(), memory=memory)
        print("本地 dry-run 控制台；不会向抖音发送。/ai 空格 问题 触发。")
        print("/pause 暂停；/resume 恢复；/quit 退出。状态文件 PAUSE 存在时仍保持暂停。")
        if not args.mock:
            print("输入的近期消息将发送给配置的 DeepSeek API 服务。")
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if line == "/quit":
                break
            if line in ("/pause", "/resume"):
                engine.paused = line == "/pause"
                print("paused" if engine.is_paused() else "resumed")
                continue
            status, reply = engine.handle(Message(
                platform="console", conversation_id=config.target_conversation,
                message_id=str(uuid.uuid4()), sender_id="local-user", sender_name="本地测试用户",
                text=line, timestamp=time.time(), is_self=False,
            ))
            print(json.dumps({"status": status, "reply": reply, "memory": engine.memory_status,
                              "recalled_memory_ids": engine.recalled_memory_ids}, ensure_ascii=False))
    except (ValueError, OSError, TypeError, MemoryStorageError, sqlite3.Error) as exc:
        # Do not echo config values, API response bodies or keys.
        print(f"启动失败（{type(exc).__name__}）：请检查配置、文件路径和本地 API 环境变量。")
        return 1
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        if store:
            store.close()
        if memory:
            memory.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
