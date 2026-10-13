"""Minimal Stud.IP JSON:API client: pagination, retries, streaming downloads."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import httpx

from . import __version__
from .models import Course, Folder, RemoteFile, Semester

log = logging.getLogger(__name__)
# httpx logs every request at INFO; keep our logs focused.
logging.getLogger("httpx").setLevel(logging.WARNING)

PAGE_LIMIT = 100
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    pass


class AuthFailed(ApiError):
    pass


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class StudipClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_url = base_url.rstrip("/") + "/jsonapi.php/v1"
        self._max_retries = max_retries
        self._sleep = sleep
        self._http = httpx.Client(
            auth=(username, password),
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Accept": "application/vnd.api+json",
                "User-Agent": f"studip-router/{__version__}",
            },
            follow_redirects=True,
            transport=transport,
        )
        self._semesters: dict[str, Semester] = {}

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> StudipClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level ---------------------------------------------------------

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Send a request with retries; caller owns the response if streaming."""
        stream = kwargs.pop("stream", False)
        for attempt in range(self._max_retries + 1):
            try:
                request = self._http.build_request(method, url, **kwargs)
                response = self._http.send(request, stream=stream)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise ApiError(f"network error for {url}: {exc}") from exc
                delay = 2**attempt
                log.warning("network error (%s), retrying in %ss", exc, delay)
                self._sleep(delay)
                continue
            if response.status_code in RETRY_STATUS and attempt < self._max_retries:
                delay = self._retry_after(response) or 2**attempt
                response.close()
                log.warning("HTTP %s for %s, retrying in %ss", response.status_code, url, delay)
                self._sleep(delay)
                continue
            if response.status_code == 401:
                response.close()
                raise AuthFailed("authentication failed (HTTP 401): wrong username or password")
            if response.status_code >= 400:
                if stream:
                    response.read()
                response.close()
                raise ApiError(f"HTTP {response.status_code} for {url}")
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _retry_after(response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        try:
            return min(float(value), 60.0) if value else None
        except ValueError:
            return None

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = path if path.startswith("http") else self.api_url + path
        return self._request("GET", url, params=params).json()

    def _paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Yield (resource, included) for every item across all pages."""
        offset = 0
        while True:
            query = {**(params or {}), "page[offset]": offset, "page[limit]": PAGE_LIMIT}
            doc = self._get_json(path, query)
            data = doc.get("data") or []
            included = doc.get("included") or []
            for item in data:
                yield item, included
            total = (doc.get("meta") or {}).get("page", {}).get("total")
            offset += len(data)
            if not data or (total is not None and offset >= total):
                return
            if total is None and len(data) < PAGE_LIMIT:
                return

    # -- resources ---------------------------------------------------------

    def me(self) -> dict[str, Any]:
        data = self._get_json("/users/me")["data"]
        return {"id": data["id"], "name": data["attributes"].get("formatted-name", "")}

    def semesters(self) -> list[Semester]:
        if not self._semesters:
            for item, _ in self._paginate("/semesters"):
                sem = self._semester_from(item)
                self._semesters[sem.id] = sem
        return sorted(self._semesters.values(), key=lambda s: s.start)

    def current_semester(self, now: datetime | None = None) -> Semester:
        sems = self.semesters()
        moment = now or datetime.now().astimezone()
        for sem in sems:
            if sem.contains(moment):
                return sem
        raise ApiError("could not determine the current semester")

    @staticmethod
    def _semester_from(item: dict[str, Any]) -> Semester:
        attrs = item["attributes"]
        return Semester(
            id=item["id"],
            title=attrs["title"],
            start=_parse_dt(attrs["start"]),
            end=_parse_dt(attrs["end"]),
        )

    def courses(self, user_id: str) -> list[Course]:
        self.semesters()  # fill cache so relationships resolve
        result = []
        for item, _ in self._paginate(f"/users/{user_id}/courses"):
            rel = item.get("relationships") or {}
            start = ((rel.get("start-semester") or {}).get("data") or {}).get("id")
            end = ((rel.get("end-semester") or {}).get("data") or {}).get("id")
            result.append(
                Course(
                    id=item["id"],
                    title=item["attributes"]["title"],
                    start_semester=self._semesters.get(start),
                    end_semester=self._semesters.get(end) if end else None,
                )
            )
        return result

    def folders(self, course_id: str) -> list[Folder]:
        result = []
        for item, _ in self._paginate(f"/courses/{course_id}/folders"):
            attrs = item["attributes"]
            parent = ((item.get("relationships") or {}).get("parent") or {}).get("data")
            result.append(
                Folder(
                    id=item["id"],
                    name=attrs.get("name", ""),
                    parent_id=parent["id"] if parent else None,
                    folder_type=attrs.get("folder-type", ""),
                )
            )
        return result

    def files(self, course_id: str) -> list[RemoteFile]:
        folders = {f.id: f for f in self.folders(course_id)}
        result = []
        for item, _ in self._paginate(f"/courses/{course_id}/file-refs"):
            attrs = item["attributes"]
            parent = ((item.get("relationships") or {}).get("parent") or {}).get("data")
            result.append(
                RemoteFile(
                    id=item["id"],
                    course_id=course_id,
                    name=attrs["name"],
                    folder_path=folder_path(folders, parent["id"] if parent else None),
                    size=int(attrs.get("filesize") or 0),
                    chdate=_parse_dt(attrs["chdate"]),
                    mime_type=attrs.get("mime-type") or "",
                    description=attrs.get("description") or "",
                    downloadable=bool(attrs.get("is-downloadable", True)),
                )
            )
        return result

    def download(self, file_id: str, sink: Callable[[bytes], None], chunk: int = 1 << 16) -> None:
        """Stream a file's content into ``sink`` chunk by chunk."""
        response = self._request("GET", f"{self.api_url}/file-refs/{file_id}/content", stream=True)
        try:
            for block in response.iter_bytes(chunk):
                sink(block)
        finally:
            response.close()


def folder_path(folders: dict[str, Folder], folder_id: str | None) -> str:
    """Path of a folder below the course root folder, e.g. "Übungen/Blatt 1"."""
    parts: list[str] = []
    seen: set[str] = set()
    while folder_id and folder_id in folders and folder_id not in seen:
        seen.add(folder_id)
        folder = folders[folder_id]
        if folder.parent_id is None:  # root folder carries the course name
            break
        parts.append(folder.name)
        folder_id = folder.parent_id
    return "/".join(reversed(parts))
