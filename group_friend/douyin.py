"""Read-only, session-bound Douyin MSAA observations.

Observed names and local sequence numbers are NOT platform account/message IDs.
This module deliberately exposes no send operation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path


class ObservationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Binding:
    process_id: int
    window_handle: int
    process_started_at: str
    window_title: str
    group_heading: str
    session_id: str

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8-sig")))

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass(frozen=True)
class ObservedText:
    text: str
    sender_name: str | None
    sender_inherited: bool
    source_path: str
    # Never manufacture platform identity from names, paths, or observation time.
    sender_id: None = None
    message_id: None = None
    timestamp: None = None
    is_self: None = None

    @property
    def signature(self):
        return hashlib.sha256(json.dumps(
            [self.sender_name, self.text], ensure_ascii=False).encode()).hexdigest()


def capture(process_id=0, *, timeout=30):
    if os.name != "nt":
        raise ObservationError("windows_required")
    script = Path(__file__).resolve().parent.parent / "scripts" / "Inspect-DouyinMsaa.ps1"
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(script), "-ProcessId", str(process_id), "-IncludeText", "-Stdout"],
            capture_output=True, timeout=timeout, encoding="utf-8-sig", errors="strict",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode:
            raise ObservationError("snapshot_process_failed")
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, UnicodeError, ValueError):
        # Never echo stderr: it may contain application text or local paths.
        raise ObservationError("snapshot_unavailable") from None


def select_window(report, handle):
    if report.get("schema") != 1 or report.get("text_included") is not True:
        raise ObservationError("unsupported_snapshot")
    windows = [w for w in report.get("windows", []) if w.get("window_handle") == handle]
    if len(windows) != 1:
        raise ObservationError("window_not_unique")
    window = windows[0]
    result = window.get("result", {})
    if result.get("Truncated") is not False or result.get("Errors"):
        raise ObservationError("incomplete_snapshot")
    if not result.get("Nodes"):
        raise ObservationError("empty_snapshot")
    return window


def parse_window(window, *, expected_title, expected_heading):
    """Parse the measured 8.7.0 layout; reject layouts we have not observed.

Rows are in reverse chronological DOM order. A missing display name can inherit
the preceding chronological name, but is explicitly marked as an inference.
Images/cards are skipped instead of interpreting their author labels as text.
"""
    nodes = window["result"]["Nodes"]
    by_path = {n["Path"]: n for n in nodes}
    if len(by_path) != len(nodes):
        raise ObservationError("duplicate_node_paths")
    if by_path.get("root", {}).get("Name") != expected_title:
        raise ObservationError("window_title_changed")
    private = private_chat_layout(nodes, expected_heading)
    if private is not None:
        return parse_private_texts(nodes, private[0])
    if sum(n.get("Name") == expected_heading for n in nodes) != 1:
        raise ObservationError("group_heading_changed")
    # Locate composer structurally: an activatable grouping with a unique
    # static placeholder; after typing, choose the same grouping by its action.
    composers = [n for n in nodes if n.get("Role") == 20 and n.get("DefaultAction") == "激活"]
    candidates = []
    for composer in composers:
        parts = composer["Path"].split("/")
        if len(parts) < 4:
            continue
        main = "/".join(parts[:-3])
        base = main + "/0/0"
        if base in by_path and composer["Path"].startswith(main + "/2/"):
            candidates.append(base)
    if len(candidates) != 1:
        raise ObservationError("chat_layout_unverified")
    base = candidates[0]
    rows = [n for n in nodes if n["Path"].startswith(base + "/")
            and n["Path"].count("/") == base.count("/") + 1]
    try:
        rows.sort(key=lambda n: int(n["Path"].rsplit("/", 1)[1]), reverse=True)
    except ValueError:
        raise ObservationError("row_layout_unverified") from None
    texts = []
    inherited_name = None
    for row in rows:
        path = row["Path"]
        if row.get("Role") != 20:
            # Date separators have their own DOM positions; they do not provide
            # an author. Unknown system events break inferred attribution.
            label = row.get("Name") or ""
            if not re.fullmatch(r"(?:刚刚|前天|昨天(?: \d{1,2}:\d{2})?|\d+分钟前|\d+小时前|\d{1,2}:\d{2}|\d{1,2}[月/]\d{1,2}日?)", label):
                inherited_name = None
            continue
        header = by_path.get(path + "/1/0", {})
        explicit = header.get("Name") if header.get("Role") == 41 else None
        if explicit:
            inherited_name = explicit
            # Avatar images belong to row/0, not the content. Admin badges at
            # row/2 move content to row/3; ordinary rows use row/2 as the body.
            body = path + ("/3" if by_path.get(path + "/2", {}).get("Role") == 41 else "/2")
        else:
            body = path + "/0"
        descendants = [n for n in nodes if n["Path"] == body or n["Path"].startswith(body + "/")]
        # Media, profile cards, emoji-only rows: do not invent text descriptions.
        if any(n.get("Role") in (30, 40, 43, 59) for n in descendants):
            continue
        content = []
        for node in descendants:
            if node.get("Role") != 41 or not node.get("Name"):
                continue
            content.append(node["Name"].replace("\u200b", ""))
        text = "".join(content)
        if text.startswith(("视频无法查看", "视频已被作者", "视频仅支持作者")):
            continue
        if text.strip():
            texts.append(ObservedText(text, inherited_name, not bool(explicit), path))
    return texts


def private_chat_layout(nodes, expected_heading):
    """Measured inline private pane: validate its header, never the contact list."""
    by_path = {n['Path']: n for n in nodes}
    candidates = []
    for node in nodes:
        if node.get('Role') != 20 or node.get('DefaultAction') != '激活':
            continue
        parts = node['Path'].split('/')
        if len(parts) < 7:
            continue
        main = '/'.join(parts[:-3])
        if node['Path'] not in (main + '/2/1/0', main + '/3/1/0'):
            continue
        pane = '/'.join(parts[:-6])
        if main != pane + '/1/0/0':
            continue
        headers = [n for n in nodes if n['Path'].startswith(pane + '/0/')
                   and n.get('Role') == 41 and n.get('Name') == expected_heading]
        if (len(headers) == 1
                and main + '/0' in by_path):
            candidates.append((main + '/0', node['Path']))
    if len(candidates) > 1:
        raise ObservationError('private_chat_not_unique')
    return candidates[0] if candidates else None


PRIVATE_TIME_LABEL = r'(?:刚刚|前天|昨天(?: \d{1,2}:\d{2})?|\d+分钟前|\d+小时前|\d{1,2}:\d{2}|\d{1,2}[月/]\d{1,2}日?)'


def private_message_base(nodes, base):
    """Unwrap the measured scroll container, without unwrapping a text bubble."""
    children = [n for n in nodes if n['Path'].startswith(base + '/')
                and n['Path'].count('/') == base.count('/') + 1]
    if len(children) != 1 or children[0].get('Role') != 20:
        return base
    wrapper = children[0]
    outer = next(n for n in nodes if n['Path'] == base)
    if outer.get('Bounds') is not None and wrapper.get('Bounds') != outer['Bounds']:
        return base
    # A sole message row can also have grouping descendants. Require the
    # observed list structure: direct timestamp separators beside message rows.
    timeline = [n for n in nodes if n['Path'].startswith(wrapper['Path'] + '/')
                and n['Path'].count('/') == wrapper['Path'].count('/') + 1]
    if (any(n.get('Role') == 41 and re.fullmatch(PRIVATE_TIME_LABEL, n.get('Name') or '') for n in timeline)
            and any(n.get('Role') == 20 for n in timeline)):
        return wrapper['Path']
    return base


def parse_private_texts(nodes, base):
    """Extract private text bubbles; identity/direction remain unknown.

    The screenshot-verified layout has an avatar branch followed by body /1,
    or a continuation body /0. Bare static system notices are excluded.
    """
    base = private_message_base(nodes, base)
    by_path = {n['Path']: n for n in nodes}
    rows = [n for n in nodes if n['Path'].startswith(base + '/')
            and n['Path'].count('/') == base.count('/') + 1 and n.get('Role') == 20]
    try:
        rows.sort(key=lambda n: int(n['Path'].rsplit('/', 1)[1]), reverse=True)
    except ValueError:
        raise ObservationError('row_layout_unverified') from None
    messages = []
    for row in rows:
        path = row['Path']
        avatar = any(n['Path'].startswith(path + '/0/') and n.get('Role') == 40 for n in nodes)
        body = path + ('/1' if avatar else '/0')
        # Notices such as friendship state have a direct static child.
        if by_path.get(body, {}).get('Role') != 20:
            continue
        descendants = [n for n in nodes if n['Path'] == body or n['Path'].startswith(body + '/')]
        if any(n.get('Role') in (30, 40, 43, 59) for n in descendants):
            continue
        text = ''.join(n.get('Name') or '' for n in descendants if n.get('Role') == 41).replace('\u200b', '')
        if text.strip():
            messages.append(ObservedText(text, None, False, path))
    return messages


class WindowsObserver:
    def __init__(self, binding, capture_fn=capture):
        self.binding = binding
        self.capture_fn = capture_fn

    def read(self):
        b = self.binding
        window = select_window(self.capture_fn(b.process_id), b.window_handle)
        if window.get("process_id") != b.process_id or window.get("process_started_at") != b.process_started_at:
            raise ObservationError("client_restarted_rebind_required")
        return parse_window(window, expected_title=b.window_title, expected_heading=b.group_heading)


class SnapshotTracker:
    """Conservative preview differences, not reliable platform deduplication.

First capture is always baseline. History loads, edits, ambiguous repeated text,
and unanchored changes stop tracking until explicitly restarted/rebound.
"""
    def __init__(self, session_id=None):
        self.session_id = session_id or str(uuid.uuid4())
        self.previous = None
        self.sequence = 0
        self.stopped = False

    def update(self, messages):
        if self.stopped:
            raise ObservationError("tracker_stopped")
        signatures = [m.signature for m in messages]
        if self.previous is None:
            self.previous = signatures
            return []
        old = self.previous
        if old == signatures:
            return []
        overlaps = [k for k in range(1, min(len(old), len(signatures)) + 1)
                    if old[-k:] == signatures[:k]]
        if len(overlaps) != 1:
            self.stopped = True
            raise ObservationError("history_or_ambiguous_change")
        overlap = overlaps[0]
        tail = signatures[overlap:]
        if not tail or any(s in old or tail.count(s) > 1 for s in tail):
            self.stopped = True
            raise ObservationError("history_or_repeated_text")
        self.previous = signatures
        events = []
        for message in messages[overlap:]:
            self.sequence += 1
            events.append((f"{self.session_id}:{self.sequence}", message))
        return events
