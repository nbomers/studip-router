"""Credential storage in the OS keychain. Passwords never touch disk otherwise."""

from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import urlsplit

import keyring
import keyring.errors
from keyring.backend import KeyringBackend
from keyring.backends.chainer import ChainerBackend

from . import paths

SERVICE = "studip-router"


class AuthError(Exception):
    pass


NO_KEYCHAIN_HINT = (
    "no usable OS keychain (common on servers, WSL and containers): install and unlock "
    "a Secret Service keyring (e.g. gnome-keyring), or set STUDIP_ROUTER_PASSWORD for "
    "headless use"
)


def _keychain_error(exc: Exception) -> AuthError:
    # Never include the password; keyring messages do not contain it.
    return AuthError(f"keychain not accessible ({type(exc).__name__}): {NO_KEYCHAIN_HINT}")


def _account(base_url: str, username: str) -> str:
    return f"{username}@{urlsplit(base_url).hostname}"


_OS_BACKENDS = {
    ("keyring.backends.macOS", "Keyring"),
    ("keyring.backends.Windows", "WinVaultKeyring"),
    ("keyring.backends.SecretService", "Keyring"),
    ("keyring.backends.libsecret", "Keyring"),
    ("keyring.backends.kwallet", "DBusKeyring"),
    ("keyring.backends.kwallet", "DBusKeyringKWallet4"),
}


def _os_keyring() -> KeyringBackend:
    """Select one OS backend and use it directly, without file-backend fallback."""
    try:
        backend = keyring.get_keyring()
        candidates = backend.backends if isinstance(backend, ChainerBackend) else [backend]
        for candidate in candidates:
            identity = (type(candidate).__module__, type(candidate).__name__)
            if identity in _OS_BACKENDS and candidate.priority > 0:
                return candidate
    except Exception as exc:
        raise _keychain_error(exc) from None
    raise AuthError(f"no supported OS keychain: {NO_KEYCHAIN_HINT}")


def get_password(base_url: str, username: str) -> str:
    """Return the stored password, or STUDIP_ROUTER_PASSWORD if set (for CI/headless use)."""
    if env := os.environ.get("STUDIP_ROUTER_PASSWORD"):
        return env
    try:
        password = _os_keyring().get_password(SERVICE, _account(base_url, username))
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None
    if not password:
        raise AuthError(
            f"no password stored for {_account(base_url, username)}; run `studip auth login`"
        )
    return password


def set_password(base_url: str, username: str, password: str) -> None:
    """Store the password; raises AuthError (never containing it) if that fails."""
    try:
        _os_keyring().set_password(SERVICE, _account(base_url, username), password)
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None


def delete_password(base_url: str, username: str) -> bool:
    try:
        _os_keyring().delete_password(SERVICE, _account(base_url, username))
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None
    return True


def keychain_problem() -> str | None:
    """Why no password can be stored in the keychain, or None if a backend is usable."""
    try:
        _os_keyring()
    except AuthError as exc:
        return str(exc)
    return None


def has_password(base_url: str, username: str) -> bool:
    if os.environ.get("STUDIP_ROUTER_PASSWORD"):
        return True
    try:
        return bool(_os_keyring().get_password(SERVICE, _account(base_url, username)))
    except (keyring.errors.KeyringError, AuthError):
        return False


# -- PDF passwords: one shared password per course, used to unlock its PDFs ---------


def _pdf_account(course_id: str, base_url: str, username: str) -> str:
    # Old pdf:<id> entries cannot be bound to a server/account safely. Do not
    # read or migrate them implicitly; users re-enter the course password once.
    url = urlsplit(base_url)
    identity = [
        paths.profile(),
        url.scheme.lower(),
        (url.hostname or "").lower(),
        url.port,
        url.path.rstrip("/"),
        username,
        course_id,
    ]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
    return f"pdf:v2:{digest}"


def get_pdf_password(course_id: str, *, base_url: str, username: str) -> str | None:
    """The course's PDF password, or None if none is stored or the keychain is locked."""
    try:
        return (
            _os_keyring().get_password(SERVICE, _pdf_account(course_id, base_url, username)) or None
        )
    except (keyring.errors.KeyringError, AuthError):
        return None


def set_pdf_password(course_id: str, password: str, *, base_url: str, username: str) -> None:
    try:
        _os_keyring().set_password(SERVICE, _pdf_account(course_id, base_url, username), password)
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None


def delete_pdf_password(course_id: str, *, base_url: str, username: str) -> bool:
    try:
        _os_keyring().delete_password(SERVICE, _pdf_account(course_id, base_url, username))
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None
    return True
