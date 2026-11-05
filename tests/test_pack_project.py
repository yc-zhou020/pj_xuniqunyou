from pathlib import Path
import runpy
import tempfile
import unittest
from zipfile import ZipFile


packager = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'zip/pack_project.py'))


class ProjectPackagingTests(unittest.TestCase):
    def test_migration_archive_keeps_local_data_and_excludes_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            included = ['run_friend.py', 'browser.local.json', 'config.local.json', '.env',
                        'group_friend/app.py', 'scripts/run.py', 'tests/test_example.py',
                        'personas/NICKY_PERSONA.md', 'config/examples/reply.json', 'docs/UBUNTU.md',
                        'state/replies.sqlite3', 'state/replies.sqlite3-wal', 'state/replies.sqlite3-shm',
                        'state/firefox-profile/cookies.sqlite', 'state/memory/user/profile.md',
                        'state/memory/groups/group/sources/chat.png', 'zip/pack_project.py']
            excluded = ['.venv/site.py', '.uv-cache/cache', '.git/config',
                        '.playwright-browsers/firefox/binary', 'zip/old.zip',
                        'group_friend/__pycache__/app.pyc', 'tests/temporary.pyc',
                        'state/browser.log', 'state/browser.log.1']
            for name in included + excluded:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(('fixture ' + name).encode())
            destination = root / 'zip/new.zip'
            self.assertEqual(packager['create_archive'](root, destination), len(included))
            with ZipFile(destination) as archive:
                self.assertEqual(set(archive.namelist()), set(included))
                self.assertEqual(archive.read('.env'), b'fixture .env')
                self.assertIsNone(archive.testzip())

    def test_existing_archive_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'run_friend.py').write_text('code')
            destination = root/'archive.zip'
            destination.write_bytes(b'keep old archive')
            with self.assertRaises(FileExistsError):
                packager['create_archive'](root, destination)
            self.assertEqual(destination.read_bytes(), b'keep old archive')
