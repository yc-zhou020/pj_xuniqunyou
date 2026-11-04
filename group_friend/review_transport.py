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

    def check_private(self, binding, source_text, text, pause_path, anchor=None):
        return self._execute(binding, source_text, text, pause_path, check_only=True, require_incoming=True, anchor=anchor)

    def send_private(self, binding, source_text, text, pause_path, anchor=None):
        return self._execute(binding, source_text, text, pause_path, require_incoming=True, anchor=anchor)

    def inspect_private(self, binding, source_text, text, pause_path, anchor=None):
        """Read draft/focus diagnostics only, including when a prior draft remains."""
        return self._execute(binding, source_text, text, pause_path, require_incoming=True, anchor=anchor, inspect_only=True)

    def _execute(self, binding, source_text, text, pause_path, *, check_only=False, require_incoming=False, anchor=None, inspect_only=False):
        self.last_diagnostics = {}
        if os.name != "nt":
            raise ObservationError("windows_required")
        root = Path(__file__).resolve().parent.parent
        request = dict(binding=asdict(binding), source=source_text, text=text, require_incoming=require_incoming, source_anchor=anchor,
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
            if inspect_only:
                command.append('-InspectOnly')
            result = subprocess.run(
                command,
                capture_output=True, timeout=60, encoding="utf-8-sig", errors="strict",
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if result.returncode:
                self.last_diagnostics = dict(stage='transport', reason='native_process_failed')
                return "unknown"
            report = json.loads(result.stdout)
            stages = {'initializing', 'preflight', 'focusing', 'typing', 'validating_draft',
                      'invoking_send', 'confirming_send', 'confirmed', 'read_only_inspection', 'read_only_preflight'}
            reasons = {'draft_mismatch', 'composer_focus_changed', 'composer_focus_not_confirmed',
                       'window_focus_changed', 'send_control_unavailable', 'send_control_obscured', 'existing_draft',
                       'source_sequence_changed', 'send_control_not_unique', 'native_operation_failed'}
            if report.get('stage') in stages:
                self.last_diagnostics['stage'] = report['stage']
            if report.get('reason') in reasons:
                self.last_diagnostics['reason'] = report['reason']
            for field in ('draft_matches', 'composer_focused', 'send_button_available'):
                if type(report.get(field)) is bool:
                    self.last_diagnostics[field] = report[field]
            return report.get("outcome", "unknown")
        except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
            self.last_diagnostics = dict(stage='transport', reason='native_response_unavailable')
            return "unknown"
        finally:
            if path is not None:
                path.unlink(missing_ok=True)
