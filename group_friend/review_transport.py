"""Manually approved sends through the measured MSAA composer."""
import json
import os
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from .douyin import ObservationError, WindowsObserver
from .review import ReviewError


class WindowsReviewedTransport:
    def preflight(self, binding, source_text):
        messages = WindowsObserver(binding).read()
        if sum(m.text == source_text for m in messages) != 1:
            raise ReviewError("source_anchor_missing_or_ambiguous")

    def send(self, binding, source_text, text, pause_path):
        return self._execute(binding, source_text, text, pause_path)

    def check(self, binding, source_text, text, pause_path):
        """Exercise the real script's preflight without focus, typing, or sending."""
        return self._execute(binding, source_text, text, pause_path, check_only=True)

    def _execute(self, binding, source_text, text, pause_path, *, check_only=False):
        if os.name != "nt":
            raise ObservationError("windows_required")
        root = Path(__file__).resolve().parent.parent
        request = dict(binding=asdict(binding), source=source_text, text=text,
                       pause_path=str(Path(pause_path).resolve()))
        temporary_root = root / "state"
        temporary_root.mkdir(exist_ok=True)
        path = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json",
                                             prefix="review-send-", dir=temporary_root, delete=False) as file:
                path = Path(file.name)
                json.dump(request, file, ensure_ascii=False)
            command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                       "-File", str(root / "scripts" / "Send-DouyinReviewedReply.ps1"), "-RequestPath", str(path)]
            if check_only:
                command.append("-CheckOnly")
            result = subprocess.run(
                command,
                capture_output=True, timeout=60, encoding="utf-8-sig", errors="strict",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                return "unknown"
            report = json.loads(result.stdout)
            return report.get("outcome", "unknown")
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
            return "unknown"
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
