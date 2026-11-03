"""Interactive credential enrollment. No password argument or plaintext fallback."""
import argparse
import getpass
import re
import sys
import warnings

from .credentials import CredentialError, WindowsCredentialStore
from .douyin_cli import emit


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message):
        # Argparse normally repeats unknown arguments, which could contain a
        # password mistakenly supplied on the command line.
        self.exit(2, "Invalid credential command. Use --help; enter secrets only at hidden prompts.\n")


def enroll(store, *, replace=False, phone_only=False, prompt=getpass.getpass):
    if not replace and store.status()["configured"]:
        raise CredentialError("already_configured_use_replace")
    # getpass can fall back to echoed input; turn that warning into a hard stop.
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        username = prompt("Login phone number (hidden): " if phone_only else "Douyin account / phone (hidden): ")
        if phone_only and not re.fullmatch(r"(?:1[3-9]\d{9}|\+[1-9]\d{6,14})", username.strip()):
            raise CredentialError("enter_login_phone_not_douyin_id")
        password = prompt("Douyin password (hidden): ")
        confirmation = prompt("Confirm password (hidden): ")
    try:
        if password != confirmation:
            raise CredentialError("password_confirmation_mismatch")
        store.save(username, password, replace=replace)
    finally:
        # Python strings cannot guarantee zeroization; release our references.
        username = password = confirmation = None


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = SafeArgumentParser(description="Dedicated Douyin account in Windows Credential Manager")
    sub = parser.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("set", help="Enter account/password locally with echo disabled")
    setup.add_argument("--replace", action="store_true")
    setup.add_argument("--phone", action="store_true", help="Require a login phone number instead of a Douyin ID")
    sub.add_parser("status", help="Show configured status and masked account hint only")
    sub.add_parser("delete", help="Remove this project's stored credential")
    args = parser.parse_args(argv)
    try:
        store = WindowsCredentialStore()
        if args.command == "set":
            if not sys.stdin.isatty():
                raise CredentialError("interactive_local_terminal_required")
            enroll(store, replace=args.replace, phone_only=args.phone)
            emit(status="saved_in_windows_credential_manager", **store.status())
        elif args.command == "status":
            emit(backend="Windows Credential Manager", **store.status())
        else:
            emit(removed=store.delete())
    except (CredentialError, getpass.GetPassWarning) as exc:
        # Our fixed error codes never contain input values.
        reason = "hidden_input_unavailable" if isinstance(exc, getpass.GetPassWarning) else str(exc)
        emit(status="stopped", reason=reason)
        return 1
    except (EOFError, KeyboardInterrupt):
        emit(status="cancelled")
        return 1
    except OSError:
        emit(status="stopped", reason="windows_credential_storage_unavailable")
        return 1
    return 0
