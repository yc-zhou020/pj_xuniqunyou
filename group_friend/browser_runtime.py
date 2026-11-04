"""Firefox-only runtime: fixed-rate sampling, queued model work, verified UI sends."""
import argparse
import asyncio
from dataclasses import replace
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

from .autonomous import InstanceLock, make_logger
from .browser_engine import BrowserEngine, PageAdapter
from .browser_session import BrowserSession, load_settings
from .browser_setup import setup
from .browser_state import BrowserState
from .browser_errors import BrowserUIError
from .config import Config, load_env, api_settings
from .conversation_snapshot import ConversationSnapshot, SNAPSHOT_RULE, digest
from .llm import DeepSeek, MockModel


async def run(settings, config, args, logger):
    state = None
    session = BrowserSession(settings)
    stop = asyncio.Event()
    tasks = []
    try:
        logger.info(json.dumps(dict(status='browser_opening', backend='firefox', sent=False)))
        await session.start()
        adapter = PageAdapter(session)
        if args.inspect:
            loop = asyncio.get_running_loop()
            start = due = loop.time()
            while True:
                for friend in settings['whitelist']:
                    await adapter.select(friend)
                    messages = await adapter.read(friend)
                    folder = Path(settings['snapshot_dir'])
                    folder.mkdir(parents=True, exist_ok=True)
                    destination = folder / (digest(friend['sec_uid'])[:24] + '.json')
                    temporary = destination.with_suffix('.tmp')
                    temporary.write_text(ConversationSnapshot(friend, time.time(), tuple(messages), ()).dumps(), encoding='utf-8')
                    temporary.replace(destination)
                    logger.info(json.dumps(dict(status='inspection_ready', target=friend['name'], messages=len(messages),
                                                native_message_ids=sum(bool(m.message_id) for m in messages),
                                                outgoing=sum(m.direction == 'outgoing' for m in messages), sent=False), ensure_ascii=False))
                if not args.duration:
                    break
                due += settings['sample_interval_seconds']
                if due - start >= args.duration:
                    break
                await asyncio.sleep(max(0, due - loop.time()))
            return 0
        state = BrowserState(settings['state_path'])
        state.prune(settings.get('retention_days', 7))
        model = MockModel() if args.mock else DeepSeek(config)
        persona = Path(config.persona_path).read_text(encoding='utf-8') + SNAPSHOT_RULE
        async def request(snapshot):
            messages = snapshot.model_messages(config.context_chars, settings.get('context_messages', 7))
            return await asyncio.to_thread(model.reply, persona, messages)
        settings = dict(settings, reply_latest=args.reply_latest)
        engine = BrowserEngine(settings, config, state, adapter, request, logger, stop=stop)
        logger.info(json.dumps(dict(status='started', backend='firefox', whitelist=[f['name'] for f in settings['whitelist']],
                                   sample_interval_seconds=settings['sample_interval_seconds'],
                                   min_send_interval_seconds=settings['min_send_interval_seconds'],
                                   dry_run=config.dry_run, delivery_blocked=state.blocked()), ensure_ascii=False))
        if args.once:
            for friend in settings['whitelist']:
                await adapter.select(friend)
                engine.sample(friend, await adapter.read(friend), reply_latest=args.reply_latest)
            while await engine.step():
                if state.blocked():
                    return 1
            return 0
        tasks = [asyncio.create_task(engine.sampler(settings['sample_interval_seconds'])),
                 asyncio.create_task(engine.dispatcher())]
        async def maintain():
            last_prune = time.monotonic()
            start = time.monotonic()
            while not engine.paused():
                if args.duration and time.monotonic() - start >= args.duration:
                    stop.set()
                    break
                if time.monotonic() - last_prune >= 3600:
                    state.prune(settings.get('retention_days', 7))
                    last_prune = time.monotonic()
                await engine.wait(.25)
            stop.set()
        tasks.append(asyncio.create_task(maintain()))
        await asyncio.gather(*tasks)
        logger.info(json.dumps(dict(status='stopped', samples=engine.samples, confirmed_sends=engine.sent,
                                   delivery_blocked=state.blocked())))
        return 0
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await session.close()
        except Exception as exc:
            # Closed browser/pipe errors must not replace Ctrl+C or the actual
            # runtime failure that led us into cleanup.
            logger.info(json.dumps(dict(status='browser_cleanup', reason=type(exc).__name__)))
        finally:
            if state:
                state.close()


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='Persistent Firefox whitelist replies, without Codex or native input focus')
    parser.add_argument('--config', default='browser.local.json')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--login', action='store_true', help='Manual login and whitelist binding only; never sends')
    modes.add_argument('--inspect', action='store_true', help='Read verified chat and save context snapshot; no model or send')
    modes.add_argument('--status', action='store_true')
    modes.add_argument('--resolve', metavar='JOB_ID')
    parser.add_argument('--outcome', choices=('visible_sent', 'not_sent'))
    parser.add_argument('--note')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--mock', action='store_true')
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--reply-latest', action='store_true')
    parser.add_argument('--duration', type=float, default=0, help='Stop after this many seconds; 0 runs until Ctrl+C/PAUSE')
    args = parser.parse_args(argv)
    logger = None
    try:
        settings = load_settings(args.config)
        if args.status or args.resolve:
            path = Path(settings['state_path'])
            if not path.exists():
                print(json.dumps(dict(jobs=[])))
                return 0
            db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
            try:
                db.row_factory = sqlite3.Row
                exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='browser_jobs'").fetchone()
                jobs = [dict(row) for row in db.execute('SELECT id,target,status,attempts,due,updated FROM browser_jobs ORDER BY rowid DESC LIMIT 20')] if exists else []
            finally:
                db.close()
            if args.status:
                names = {friend['sec_uid']: friend['name'] for friend in settings['whitelist']}
                for job in jobs:
                    job['target'] = names.get(job['target'], 'unknown')
                print(json.dumps(dict(jobs=jobs), ensure_ascii=False))
                return 0
            if not args.outcome or not args.note or not args.note.strip():
                raise ValueError('manual_note_required')
            with InstanceLock('state/autonomous.lock'):
                db = sqlite3.connect(path)
                try:
                    with db:
                        changed = db.execute("UPDATE browser_jobs SET status=?,snapshot=NULL,reply=NULL,updated=? WHERE id=? AND status IN ('sending','unknown') AND updated<=?",
                                             (args.outcome, time.time(), args.resolve, time.time() - 90)).rowcount
                    if changed != 1:
                        raise ValueError('send_not_uncertain_or_too_recent')
                finally:
                    db.close()
                make_logger(settings['log_path']).info(json.dumps(dict(status='manually_resolved', job=args.resolve,
                    outcome=args.outcome, note=args.note[:500]), ensure_ascii=False))
            return 0
        if args.login:
            with InstanceLock('state/autonomous.lock'):
                return asyncio.run(setup(args.config))
        if any(not f.get('sec_uid') or not f.get('douyin_id') for f in settings['whitelist']):
            print(json.dumps(dict(status='manual_login_binding_required', command='uv run --locked python run_friend.py --login')))
            return 1
        config = Config.load(settings['reply_config'])
        if args.dry_run or args.mock:
            config = replace(config, dry_run=True)
        if config.memory_enabled:
            raise ValueError('browser_memory_must_be_disabled_until_explicitly_linked')
        if not args.inspect and not args.mock:
            load_env()
            if api_settings()[1] != settings.get('allowed_api_base', 'https://api.deepseek.com'):
                raise ValueError('api_destination_changed')
        if not math.isfinite(args.duration) or args.duration < 0:
            raise ValueError('invalid_duration')
        logger = make_logger(settings['log_path'])
        with InstanceLock('state/autonomous.lock'):
            return asyncio.run(run(settings, config, args, logger))
    except KeyboardInterrupt:
        if logger:
            logger.info('stopped_by_user')
        return 0
    except Exception as exc:
        message = json.dumps(dict(status='stopped', reason=str(exc) if isinstance(exc, BrowserUIError) else type(exc).__name__))
        if logger:
            logger.error(message)
        else:
            print(message)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
