"""Credential storage in the OS keychain. Passwords never touch disk otherwise."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import keyring
import keyring.errors

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


def get_password(base_url: str, username: str) -> str:
    """Return the stored password, or STUDIP_ROUTER_PASSWORD if set (for CI/headless use)."""
    if env := os.environ.get("STUDIP_ROUTER_PASSWORD"):
        return env
    try:
        password = keyring.get_password(SERVICE, _account(base_url, username))
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
        keyring.set_password(SERVICE, _account(base_url, username), password)
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None


def delete_password(base_url: str, username: str) -> bool:
    try:
        keyring.delete_password(SERVICE, _account(base_url, username))
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None
    return True


def keychain_problem() -> str | None:
    """Why no password can be stored in the keychain, or None if a backend is usable."""
    try:
        backend = keyring.get_keyring()
    except Exception as exc:  # pragma: no cover - broken keyring configuration
        return f"{type(exc).__name__}: {NO_KEYCHAIN_HINT}"
    priority = getattr(backend, "priority", 1)
    module = type(backend).__module__
    if module.startswith(("keyring.backends.fail", "keyring.backends.null")) or priority <= 0:
        return f"{type(backend).__name__} backend: {NO_KEYCHAIN_HINT}"
    return None


def has_password(base_url: str, username: str) -> bool:
    if os.environ.get("STUDIP_ROUTER_PASSWORD"):
        return True
    try:
        return bool(keyring.get_password(SERVICE, _account(base_url, username)))
    except keyring.errors.KeyringError:
        return False


# -- PDF passwords: one shared password per course, used to unlock its PDFs ---------


def _pdf_account(course_id: str) -> str:
    return f"pdf:{course_id}"


def get_pdf_password(course_id: str) -> str | None:
    """The course's PDF password, or None if none is stored or the keychain is locked."""
    try:
        return keyring.get_password(SERVICE, _pdf_account(course_id)) or None
    except keyring.errors.KeyringError:
        return None


def set_pdf_password(course_id: str, password: str) -> None:
    try:
        keyring.set_password(SERVICE, _pdf_account(course_id), password)
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None


def delete_pdf_password(course_id: str) -> bool:
    try:
        keyring.delete_password(SERVICE, _pdf_account(course_id))
    except keyring.errors.PasswordDeleteError:
        return False
    except keyring.errors.KeyringError as exc:
        raise _keychain_error(exc) from None
    return True
