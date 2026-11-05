"""Firefox-only runtime: fixed-rate sampling, queued model work, verified UI sends."""
import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import asdict, replace
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

from .runtime_support import InstanceLock, make_logger
from .browser_engine import BrowserEngine, PageAdapter
from .browser_session import BrowserSession, load_settings
from .browser_setup import setup
from .browser_state import BrowserState
from .reply_worker import ReplyWorker
from .browser_errors import BrowserUIError
from .config import Config, load_env, api_settings
from .conversation_snapshot import ConversationSnapshot, SNAPSHOT_RULE, digest, ContinuityError
from .llm import DeepSeek, MockModel
from .targets import active_targets, target_key, is_group
from .browser_reference.douyin_chat import FriendNotFound
from .markdown_memory import MarkdownMemory, MEMORY_RULE


async def run(settings, config, args, logger):
    state = None
    engine = None
    shutdown_reason = 'completed'
    stage = args.stage
    receives = stage in ('all', 'receive')
    generates = stage in ('all', 'generate')
    sends = stage in ('all', 'send')
    session = BrowserSession(settings) if stage != 'generate' or args.inspect else None
    stop = asyncio.Event()
    tasks = []
    try:
        adapter = None
        if session is not None:
            logger.info(json.dumps(dict(status='browser_opening', backend='firefox', sent=False)))
            await session.start()
            adapter = PageAdapter(session)
        if args.inspect:
            loop = asyncio.get_running_loop()
            start = due = loop.time()
            while True:
                for friend in active_targets(settings):
                    try:
                        await adapter.select(friend)
                        messages = await adapter.read(friend)
                    except FriendNotFound:
                        logger.info(json.dumps(dict(status='target_waiting', target=friend['name'], reason='conversation_not_visible')))
                        continue
                    folder = Path(settings['snapshot_dir'])
                    folder.mkdir(parents=True, exist_ok=True)
                    destination = folder / (digest(target_key(friend))[:24] + '.json')
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
        recover = tuple(name for name, enabled in (('generate', generates), ('send', sends)) if enabled)
        state = BrowserState(settings['state_path'], recover=recover)
        state.prune(settings.get('retention_days', 7))
        memory = MarkdownMemory(config) if config.memory_enabled else None
        model = (MockModel() if args.mock else DeepSeek(config)) if generates else None
        persona = Path(config.persona_path).read_text(encoding='utf-8') + SNAPSHOT_RULE if generates else None
        if memory is not None and generates:
            persona += MEMORY_RULE
        async def request(snapshot):
            messages = snapshot.model_messages(config.context_chars, settings.get('context_messages', 7))
            if memory is not None:
                if not config.dry_run:
                    try:
                        await asyncio.to_thread(memory.capture, snapshot)
                    except Exception as exc:
                        logger.info(json.dumps(dict(status='memory_unavailable', stage='write', reason=type(exc).__name__)))
                try:
                    context, decision = await asyncio.to_thread(memory.context_with_decision, snapshot)
                    logger.info(json.dumps(dict(status='memory_recall', target=snapshot.friend['name'],
                                                **asdict(decision)), ensure_ascii=False))
                    if context is not None:
                        messages.insert(0, context)
                except Exception as exc:
                    logger.info(json.dumps(dict(status='memory_unavailable', stage='read', reason=type(exc).__name__)))
            return await asyncio.to_thread(model.reply, persona, messages)
        settings = dict(settings, reply_latest=args.reply_latest)
        engine = BrowserEngine(settings, config, state, adapter, request, logger, stop=stop, memory=memory)
        worker = ReplyWorker(settings, config, state, request, logger, stop=stop)
        logger.info(json.dumps(dict(status='started', stage=stage, backend='firefox' if session else None, whitelist=[f['name'] for f in active_targets(settings)],
                                   sample_interval_seconds=settings['sample_interval_seconds'],
                                   min_send_interval_seconds=settings['min_send_interval_seconds'],
                                   dry_run=config.dry_run, delivery_blocked=state.blocked()), ensure_ascii=False))
        if memory is not None:
            logger.info(json.dumps(dict(status='memory_ready', path=str(memory.root), format='markdown',
                                       writable=not config.dry_run), ensure_ascii=False))
        if args.once:
            if receives:
                for friend in active_targets(settings):
                    try:
                        await adapter.select(friend)
                        engine.sample(friend, await adapter.read(friend), reply_latest=args.reply_latest)
                    except FriendNotFound:
                        engine.event('target_waiting', target=friend['name'], reason='conversation_not_visible')
                    except ContinuityError as exc:
                        engine.event('observation_waiting', target=friend['name'], reason=str(exc))
            while not engine.paused():
                worked = await worker.step() if generates else False
                if sends:
                    worked = await engine.send_step() or worked
                    if state.blocked():
                        return 1
                if not worked:
                    break
            return 0
        if receives:
            tasks.append(asyncio.create_task(engine.sampler(settings['sample_interval_seconds'])))
        if generates:
            tasks.append(asyncio.create_task(worker.run()))
        if sends:
            tasks.append(asyncio.create_task(engine.dispatcher()))
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
        return 0
    except asyncio.CancelledError:
        shutdown_reason = 'interrupted'
        raise
    except Exception:
        shutdown_reason = 'error'
        raise
    finally:
        stop.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if state is not None and engine is not None:
            logger.info(json.dumps(dict(status='stopped', reason=shutdown_reason,
                                       samples=engine.samples, confirmed_sends=engine.sent,
                                       delivery_blocked=state.blocked())))
        try:
            if session is not None:
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
    parser.add_argument('--stage', choices=('all', 'receive', 'generate', 'send'), default='all',
                        help='Run the full pipeline or one durable stage; generate needs no browser')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--login', action='store_true', help='Manual login and whitelist binding only; never sends')
    modes.add_argument('--inspect', action='store_true', help='Read verified chat and save context snapshot; no model or send')
    modes.add_argument('--status', action='store_true')
    modes.add_argument('--resolve', metavar='JOB_ID')
    parser.add_argument('--outcome', choices=('visible_sent', 'not_sent'))
    parser.add_argument('--note')
    delivery = parser.add_mutually_exclusive_group()
    delivery.add_argument('--live', action='store_true', help='Enable real replies for this run; do not change local config')
    delivery.add_argument('--dry-run', action='store_true')
    delivery.add_argument('--mock', action='store_true')
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
                names = {target_key(friend): friend['name'] for friend in active_targets(settings)}
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
        if any(not f.get('sec_uid') or not (f.get('douyin_id') or f.get('user_id'))
               for f in settings['whitelist'] if not f.get('is_self')):
            print(json.dumps(dict(status='manual_login_binding_required', command='uv run run_friend.py --login')))
            return 1
        config = Config.load(settings['reply_config'])
        if args.live:
            config = replace(config, dry_run=False)
        if args.dry_run or args.mock:
            config = replace(config, dry_run=True)
        if args.stage in ('all', 'generate') and not args.inspect and not args.mock:
            load_env()
            if api_settings()[1] != settings.get('allowed_api_base', 'https://api.deepseek.com'):
                raise ValueError('api_destination_changed')
        if not math.isfinite(args.duration) or args.duration < 0:
            raise ValueError('invalid_duration')
        logger = make_logger(settings['log_path'])
        with ExitStack() as locks:
            # The persistent Firefox profile has one owner. The browser-free model
            # stage may run alongside receive/send, with its own singleton lock.
            if args.stage != 'generate' or args.inspect:
                locks.enter_context(InstanceLock('state/autonomous.lock'))
            if args.stage in ('all', 'generate') and not args.inspect:
                locks.enter_context(InstanceLock('state/model.lock'))
            if not args.inspect:
                mode = '只采集，不调用模型或发送' if args.stage == 'receive' else (
                    '演练，不发送' if config.dry_run else '真实模型生成，回复入队' if args.stage == 'generate' else '真实发送')
                logger.info('运行模式：' + mode + '；白名单：' + '、'.join(f['name'] for f in active_targets(settings)))
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
