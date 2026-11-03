"""Windows Credential Manager storage; secrets never enter project files or CLI args."""
import ctypes
import os
from dataclasses import dataclass, field


CREDENTIAL_TARGET = "pj_DeepSeek/douyin/bot"
ERROR_NOT_FOUND = 1168


class CredentialError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _Credential(ctypes.Structure):
    _fields_ = [
        ("Flags", ctypes.c_uint32), ("Type", ctypes.c_uint32),
        ("TargetName", ctypes.c_wchar_p), ("Comment", ctypes.c_wchar_p),
        ("LastWritten", _FileTime), ("CredentialBlobSize", ctypes.c_uint32),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", ctypes.c_uint32), ("AttributeCount", ctypes.c_uint32),
        ("Attributes", ctypes.c_void_p), ("TargetAlias", ctypes.c_wchar_p),
        ("UserName", ctypes.c_wchar_p),
    ]


def account_hint(username):
    if len(username) <= 4:
        return "***"
    return username[:2] + "***" + username[-2:]


class WindowsCredentialStore:
    def __init__(self, target=CREDENTIAL_TARGET):
        if os.name != "nt":
            raise CredentialError("windows_required")
        if not target.startswith("pj_DeepSeek/douyin/") or "\x00" in target or len(target) > 200:
            raise CredentialError("invalid_project_credential_target")
        self.target = target
        self.api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self.api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), ctypes.c_uint32]
        self.api.CredWriteW.restype = ctypes.c_int
        self.api.CredReadW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.POINTER(_Credential))]
        self.api.CredReadW.restype = ctypes.c_int
        self.api.CredDeleteW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
        self.api.CredDeleteW.restype = ctypes.c_int
        self.api.CredFree.argtypes = [ctypes.c_void_p]
        self.api.CredFree.restype = None

    def _read(self, include_password=False):
        pointer = ctypes.POINTER(_Credential)()
        if not self.api.CredReadW(self.target, 1, 0, ctypes.byref(pointer)):
            code = ctypes.get_last_error()
            if code == ERROR_NOT_FOUND:
                return None
            raise CredentialError(f"credential_read_failed_{code}")
        try:
            record = pointer.contents
            username = record.UserName or ""
            if include_password:
                if not record.CredentialBlob or not 0 < record.CredentialBlobSize <= 2560:
                    raise CredentialError("invalid_stored_credential")
                try:
                    password = ctypes.string_at(record.CredentialBlob, record.CredentialBlobSize).decode("utf-16-le")
                except UnicodeError:
                    raise CredentialError("invalid_stored_credential") from None
                return AccountCredentials(username, password)
            return dict(configured=True, account_hint=account_hint(username), target=self.target)
        finally:
            record = pointer.contents
            if record.CredentialBlob and record.CredentialBlobSize <= 2560:
                ctypes.memset(record.CredentialBlob, 0, record.CredentialBlobSize)
            self.api.CredFree(pointer)

    def status(self):
        return self._read() or dict(configured=False, target=self.target)

    def load(self):
        credentials = self._read(include_password=True)
        if credentials is None:
            raise CredentialError("account_not_configured")
        return credentials

    def save(self, username, password, *, replace=False):
        if (not isinstance(username, str) or not username.strip() or "\x00" in username
                or not isinstance(password, str) or not password or "\x00" in password):
            raise CredentialError("account_and_password_required")
        username = username.strip()
        try:
            if len(username.encode("utf-16-le")) > 1024:
                raise CredentialError("account_too_long")
            encoded = bytearray(password.encode("utf-16-le"))
        except UnicodeError:
            raise CredentialError("invalid_unicode_credential") from None
        try:
            if len(encoded) > 2560:
                raise CredentialError("password_too_long")
            if not replace and self.status()["configured"]:
                raise CredentialError("already_configured_use_replace")
            buffer = (ctypes.c_ubyte * len(encoded)).from_buffer(encoded)
            record = _Credential()
            record.Type = 1  # CRED_TYPE_GENERIC
            record.TargetName = self.target
            record.Comment = "Dedicated Douyin account for pj_DeepSeek"
            record.UserName = username
            record.CredentialBlobSize = len(encoded)
            record.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
            record.Persist = 2  # Same Windows user, subsequent logons on this computer.
            if not self.api.CredWriteW(ctypes.byref(record), 0):
                raise CredentialError(f"credential_write_failed_{ctypes.get_last_error()}")
        finally:
            if encoded:
                ctypes.memset((ctypes.c_ubyte * len(encoded)).from_buffer(encoded), 0, len(encoded))

    def delete(self):
        if self.api.CredDeleteW(self.target, 1, 0):
            return True
        code = ctypes.get_last_error()
        if code == ERROR_NOT_FOUND:
            return False
        raise CredentialError(f"credential_delete_failed_{code}")
