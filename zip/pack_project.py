"""Package migration files: uv run zip/pack_project.py (standard library only)."""
import argparse
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DIRECTORIES = ('group_friend', 'scripts', 'tests', 'personas', 'config', 'docs', 'state')
FILES = ('run_friend.py', '.env', '.env.example', 'pyproject.toml', 'uv.lock',
         'uv.toml', '.python-version', 'README.md', 'CODEX_HANDOFF.md',
         'THIRD_PARTY_NOTICES.md', 'zip/pack_project.py', 'zip/README.md')


def collect_files(root):
    """Use the Bash command's explicit allowlist; never traverse old archives."""
    root = Path(root).resolve()
    candidates = {root / name for name in FILES}
    candidates.update(root.glob('*.json'))
    for name in DIRECTORIES:
        folder = root / name
        if folder.is_dir() and not folder.is_symlink():
            candidates.update(folder.rglob('*'))
    selected = []
    for path in candidates:
        relative = path.relative_to(root)
        if ('__pycache__' in relative.parts or path.suffix == '.pyc'
                or (relative.parent == Path('state') and relative.name.startswith('browser.log'))):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if not path.resolve().is_relative_to(root):
            continue
        selected.append(path)
    return sorted(selected, key=lambda path: path.relative_to(root).as_posix())


def create_archive(root, destination):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    files = collect_files(root)
    files = [path for path in files if path.resolve() != destination]
    if not files:
        raise ValueError('no_project_files')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves older archives, including same-name files.
    with destination.open('xb') as stream:
        try:
            with ZipFile(stream, mode='w', compression=ZIP_DEFLATED, compresslevel=6) as archive:
                for path in files:
                    archive.write(path, arcname=path.relative_to(root).as_posix())
            # Validate CRCs without displaying any archived configuration data.
            stream.flush()
            with ZipFile(destination) as archive:
                if archive.testzip() is not None:
                    raise ValueError('archive_integrity_check_failed')
        except BaseException:
            stream.close()
            destination.unlink(missing_ok=True)
            raise
    return len(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description='打包必要迁移文件到项目 zip/，包含本地配置、记忆和登录资料')
    parser.parse_args(argv)
    sys.path.insert(0, str(PROJECT_ROOT))
    from group_friend.runtime_support import InstanceLock
    folder = PROJECT_ROOT / 'zip'
    folder.mkdir(exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    destination = folder / f'pj_xuniqunyou_{stamp}.zip'
    serial = 1
    while destination.exists():
        destination = folder / f'pj_xuniqunyou_{stamp}_{serial}.zip'
        serial += 1
    try:
        with ExitStack() as locks:
            for name in ('autonomous.lock', 'model.lock', 'group-memory-init.lock'):
                locks.enter_context(InstanceLock(PROJECT_ROOT / 'state' / name))
            count = create_archive(PROJECT_ROOT, destination)
        print(f'打包完成：{destination}')
        print(f'共 {count} 个文件，{destination.stat().st_size / (1024 * 1024):.2f} MB；ZIP 完整性检查通过。')
        return 0
    except KeyboardInterrupt:
        print('打包已中断，未完成的压缩包已清理。')
        return 130
    except ValueError as exc:
        if str(exc) == 'runtime_already_running':
            print('请先停止主程序、独立模型进程和群记忆初始化程序，再打包。')
        else:
            print('打包失败：' + str(exc))
        return 1
    except OSError as exc:
        print('打包失败：' + type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
