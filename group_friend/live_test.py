"""Opt-in live test: `uv run scripts/test_live_reply.py`; real model calls and private replies."""
import argparse
import asyncio
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
from contextlib import ExitStack

from group_friend.browser_runtime import run
from group_friend.browser_session import load_settings
from group_friend.config import Config, api_settings, load_env
from group_friend.runtime_support import InstanceLock, make_logger
from group_friend.markdown_memory import MarkdownMemory
from group_friend.targets import active_targets


def prepare(path):
    destination = Path(path)
    if not destination.exists():
        destination.write_text(Path('config/examples/browser.json').read_text(encoding='utf-8'), encoding='utf-8')
    settings = load_settings(destination)
    reply_path = Path(settings['reply_config'])
    if not reply_path.exists():
        reply_path.write_text(Path('config/examples/reply.json').read_text(encoding='utf-8'), encoding='utf-8')
    if not Path('.env').exists():
        Path('.env').write_text(Path('.env.example').read_text(encoding='utf-8'), encoding='utf-8')
    print('本地配置已准备；已有文件未覆盖。请填写 .env 并完成手动登录和白名单绑定。')


def prerequisites(path):
    if not Path(path).is_file():
        print('缺少本地配置。先运行：uv run scripts/test_live_reply.py --prepare')
        return None
    settings = load_settings(path)
    print('已启用白名单：' + '、'.join(friend['name'] for friend in active_targets(settings)))
    missing = []
    if any(not friend.get('sec_uid') or not (friend.get('douyin_id') or friend.get('user_id'))
           for friend in settings['whitelist'] if not friend.get('is_self')):
        missing.append('手动登录并绑定白名单：uv run run_friend.py --config ' + str(path) + ' --login')
    if not Path(settings['profile_dir']).is_dir():
        missing.append('缺少 Firefox 登录资料，需要先完成手动登录。')
    reply_path = Path(settings['reply_config'])
    config = Config.load(reply_path) if reply_path.is_file() else None
    if config is None:
        missing.append('缺少回复配置：' + str(reply_path))
    elif not Path(config.persona_path).is_file():
        missing.append('请确认 persona_path 文件存在。')
    elif Path(config.pause_path).exists():
        missing.append('暂停文件存在，请人工核对状态后移除：' + config.pause_path)
    if config is not None and config.memory_enabled:
        try:
            MarkdownMemory(config)
        except ValueError:
            missing.append('memory_path 必须指向 Markdown 记忆目录，例如 state/memory。')
    load_env()
    key = os.environ.get('DEEPSEEK_API_KEY', '').strip()
    try:
        _, base, _ = api_settings()
        if base != settings.get('allowed_api_base', 'https://api.deepseek.com'):
            missing.append('DeepSeek 地址与允许的地址不一致，请核对配置。')
        if not key or key.lower().startswith(('your', 'replace', 'sk-your')):
            missing.append('请在 .env 填写真实 DEEPSEEK_API_KEY。')
    except ValueError:
        missing.append('请在 .env 填写有效的 DeepSeek Key、模型及 HTTPS 地址。')
    state_path = Path(settings['state_path'])
    if state_path.exists():
        db = sqlite3.connect(state_path.resolve().as_uri() + '?mode=ro', uri=True)
        try:
            for table, column in (('browser_jobs', 'status'), ('auto_jobs', 'status'), ('replies', 'state')):
                if db.execute('SELECT 1 FROM sqlite_master WHERE name=?', (table,)).fetchone():
                    states = ('pending', 'generating', 'generated', 'sending', 'unknown') if table == 'browser_jobs' else ('sending', 'unknown')
                    marks = ','.join('?' for _ in states)
                    if db.execute(f'SELECT 1 FROM {table} WHERE {column} IN ({marks}) LIMIT 1', states).fetchone():
                        missing.append('已有未完成任务或未知发送。请先用 uv run run_friend.py --status 核对并处理；测试不会清除回执。')
                        break
        finally:
            db.close()
    # Resolve the installed executable without launching a browser or reading cookies.
    from playwright.sync_api import sync_playwright
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(settings['browser_path']).resolve())
    with sync_playwright() as driver:
        if not Path(driver.firefox.executable_path).is_file():
            missing.append('缺少项目 Firefox。运行：uv run scripts/test_live_reply.py --install-browser')
    for item in missing:
        print('需要协助：' + item)
    return (settings, config) if not missing else None


class TestLogger:
    def __init__(self, logger):
        self.logger = logger
        self.confirmed = 0
        self.sampled = set()
        self.blocked = False

    def info(self, text):
        self.logger.info(text)
        try:
            event = json.loads(text)
        except (TypeError, ValueError):
            return
        if event.get('status') == 'ready':
            target = event.get('target')
            if target not in self.sampled:
                self.sampled.add(target)
                print(f'采样就绪：{target}。现在请该用户发送一条新文字消息。', flush=True)
        if event.get('status') == 'visible_sent':
            self.confirmed += 1
        if event.get('status') in ('unknown', 'delivery_blocked'):
            self.blocked = True


def main(argv=None):
    parser = argparse.ArgumentParser(description='真实白名单私聊/群聊回复测试；会调用 DeepSeek 并真实发送')
    parser.add_argument('--config', default='browser.local.json')
    parser.add_argument('--duration', type=float, default=180, help='等待和处理新消息的秒数，默认 180')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--prepare', action='store_true', help='只创建缺失的本地配置，不覆盖已有文件')
    modes.add_argument('--check', action='store_true', help='只检查运行条件，不访问抖音或模型')
    modes.add_argument('--install-browser', action='store_true', help='安装项目 Firefox，不登录、不发送')
    args = parser.parse_args(argv)
    try:
        if args.prepare:
            prepare(args.config)
            return 0
        if args.install_browser:
            settings = load_settings(args.config)
            os.environ['PLAYWRIGHT_BROWSERS_PATH'] = str(Path(settings['browser_path']).resolve())
            from playwright.__main__ import main as playwright_main
            sys.argv = ['playwright', 'install', 'firefox']
            playwright_main()
            return 0
        if not math.isfinite(args.duration) or args.duration <= 0:
            raise ValueError('duration must be finite and positive')
        # Hold both production locks through preflight and execution.
        with ExitStack() as locks:
            locks.enter_context(InstanceLock('state/autonomous.lock'))
            locks.enter_context(InstanceLock('state/model.lock'))
            ready = prerequisites(args.config)
            if ready is None:
                return 1
            if args.check:
                print('运行条件检查通过；尚未验证真实网页登录状态。')
                return 0
            settings, config = ready
            logger = TestLogger(make_logger(settings['log_path']))
            print('开始真实收发测试：本次启用发送，不受回复配置 dry_run 控制；请等待采样就绪提示。', flush=True)
            runtime_args = argparse.Namespace(stage='all', inspect=False, mock=False, once=False,
                                              reply_latest=False, duration=args.duration)
            result = asyncio.run(run(settings, replace(config, dry_run=False), runtime_args, logger))
            if result == 0 and logger.confirmed and not logger.blocked:
                print(f'PASS：确认发送 {logger.confirmed} 条白名单回复。')
                return 0
            print('FAIL：出现未知发送或在测试时间内未确认回复。请查看日志并核对是否有新来信。')
            return 2
    except KeyboardInterrupt:
        print('测试已由用户停止，未判定通过。')
        return 130
    except Exception as exc:
        # Do not print arbitrary exception bodies: they can contain private data.
        print('测试未完成：' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
