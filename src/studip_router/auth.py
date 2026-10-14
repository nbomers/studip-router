"""Credential storage in the OS keychain. Passwords never touch disk otherwise."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import keyring
import keyring.errors

SERVICE = "studip-router"


class AuthError(Exception):
    pass


def _account(base_url: str, username: str) -> str:
    return f"{username}@{urlsplit(base_url).hostname}"


def get_password(base_url: str, username: str) -> str:
    """Return the stored password, or STUDIP_ROUTER_PASSWORD if set (for CI/headless use)."""
    if env := os.environ.get("STUDIP_ROUTER_PASSWORD"):
        return env
    try:
        password = keyring.get_password(SERVICE, _account(base_url, username))
    except keyring.errors.KeyringError as exc:
        raise AuthError(f"keychain not accessible: {exc}") from exc
    if not password:
        raise AuthError(
            f"no password stored for {_account(base_url, username)}; run `studip auth login`"
        )
    return password


def set_password(base_url: str, username: str, password: str) -> None:
    keyring.set_password(SERVICE, _account(base_url, username), password)


def delete_password(base_url: str, username: str) -> bool:
    try:
        keyring.delete_password(SERVICE, _account(base_url, username))
    except keyring.errors.PasswordDeleteError:
        return False
    return True


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
    keyring.set_password(SERVICE, _pdf_account(course_id), password)


def delete_pdf_password(course_id: str) -> bool:
    try:
        keyring.delete_password(SERVICE, _pdf_account(course_id))
    except keyring.errors.PasswordDeleteError:
        return False
    return True
