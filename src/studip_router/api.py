"""Minimal Stud.IP JSON:API client: pagination, retries, streaming downloads."""

from __future__ import annotations

import logging
import ssl
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from . import __version__
from .models import Course, Folder, RemoteFile, Semester

log = logging.getLogger(__name__)
# httpx logs every request at INFO; keep our logs focused.
logging.getLogger("httpx").setLevel(logging.WARNING)

PAGE_LIMIT = 100
MAX_PAGES = 1000  # 100 000 records: far more than any course has
_sleep = time.sleep  # replaced in tests
RETRY_STATUS = {429, 500, 502, 503, 504}


class ApiError(Exception):
    pass


class AuthFailed(ApiError):
    pass


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.astimezone()


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def base_url_problem(url: str) -> str | None:
    """Why ``url`` must not receive the password, or None if it is fine.

    Basic Auth sends the password with every request, so plain HTTP is only
    allowed for a Stud.IP running on this machine.
    """
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return None
    if parts.scheme == "http" and parts.hostname in LOCAL_HOSTS:
        return None
    if parts.scheme == "http":
        return "must start with https:// (http:// would send your password unencrypted)"
    return "must start with https://"


def ssl_context() -> ssl.SSLContext | bool:
    """Use the operating system's certificate store (works with university CAs and
    TLS-inspecting proxies that are trusted by the OS but unknown to certifi)."""
    try:
        import truststore

        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # pragma: no cover - fall back to httpx's certifi bundle
        return True


def _dict(value: Any) -> dict[str, Any]:
    """PHP's json_encode writes an empty object as []: treat anything else as {}."""
    return value if isinstance(value, dict) else {}


def _rel_id(relationships: Any, name: str) -> str | None:
    data = _dict(_dict(relationships).get(name)).get("data")
    return data.get("id") if isinstance(data, dict) else None


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
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if problem := base_url_problem(base_url):
            raise ApiError(f"Stud.IP address {base_url!r} {problem}")
        self.api_url = base_url.rstrip("/") + "/jsonapi.php/v1"
        self._max_retries = max_retries
        self._sleep = sleep or (lambda seconds: _sleep(seconds))
        self._http = httpx.Client(
            auth=(username, password),
            verify=ssl_context(),
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers={
                "Accept": "application/vnd.api+json",
                "User-Agent": f"studip-router/{__version__}",
            },
            follow_redirects=True,
            transport=transport,
            event_hooks={"request": [self._only_secure]},
        )
        self._semesters: dict[str, Semester] = {}
        self.warnings: list[str] = []  # records skipped because the API sent surprises

    def close(self) -> None:
        self._http.close()

    def _only_secure(self, request: httpx.Request) -> None:
        """Every request, redirects included, stays on https (http only for localhost):
        a redirect to plain http would let anyone on the network swap the file."""
        url = request.url
        if url.scheme == "https" or (url.scheme == "http" and url.host in LOCAL_HOSTS):
            return
        raise ApiError(f"refused to follow a redirect to {url.scheme}://{url.host}")

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
        response = self._request("GET", url, params=params)
        try:
            doc = response.json()
        except ValueError:
            kind = response.headers.get("content-type", "").split(";")[0] or "no content type"
            raise ApiError(
                f"Stud.IP sent {kind} instead of JSON for {url} - a single sign-on (SSO) "
                "login page, a maintenance page, or the JSON:API is not enabled"
            ) from None
        if not isinstance(doc, dict):
            raise ApiError(f"unexpected JSON from {url}")
        return doc

    def _paginate(
        self, path: str, params: dict[str, Any] | None = None
    ) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
        """Yield (resource, included) for every item across all pages.

        Each id is yielded once. A server that ignores ``page[offset]`` (a page with
        nothing new) or never ends (more than MAX_PAGES) is stopped, not followed.
        """
        offset = 0
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            query = {**(params or {}), "page[offset]": offset, "page[limit]": PAGE_LIMIT}
            doc = self._get_json(path, query)
            data = doc.get("data") or []
            if not isinstance(data, list):
                raise ApiError(f"unexpected JSON from {path}: data is not a list")
            included = doc.get("included") or []
            new = 0
            for item in data:
                item_id = item.get("id") if isinstance(item, dict) else None
                if isinstance(item_id, str):
                    if item_id in seen:
                        continue
                    seen.add(item_id)
                new += 1
                yield item, included
            total = _dict(_dict(doc.get("meta")).get("page")).get("total")
            offset += len(data)
            if not data or (total is not None and offset >= total):
                return
            if total is None and len(data) < PAGE_LIMIT:
                return
            if not new:
                self.warnings.append(f"{path}: the server repeated a page; stopped there")
                return
        raise ApiError(f"{path}: more than {MAX_PAGES} pages, stopped (server error?)")

    # -- resources ---------------------------------------------------------

    def me(self) -> dict[str, Any]:
        data = _dict(self._get_json("/users/me").get("data"))
        if not isinstance(data.get("id"), str):
            raise ApiError("unexpected answer from Stud.IP for /users/me (no user id)")
        return {"id": data["id"], "name": _dict(data.get("attributes")).get("formatted-name", "")}

    def semesters(self) -> list[Semester]:
        if not self._semesters:
            for item, _ in self._paginate("/semesters"):
                try:
                    sem = self._semester_from(item)
                except (KeyError, TypeError, ValueError, AttributeError) as exc:
                    self._skipped(item, exc)
                    continue
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
        start, end = _parse_dt(attrs["start"]), _parse_dt(attrs["end"])
        if start is None or end is None:
            raise ValueError("semester without start or end")
        return Semester(id=item["id"], title=attrs["title"], start=start, end=end)

    def courses(self, user_id: str) -> list[Course]:
        self.semesters()  # fill cache so relationships resolve
        result = []
        for item, _ in self._paginate(f"/users/{user_id}/courses"):
            try:
                rel = _dict(item.get("relationships"))
                start = _rel_id(rel, "start-semester")
                end = _rel_id(rel, "end-semester")
                attrs = item["attributes"]
                result.append(
                    Course(
                        id=item["id"],
                        title=attrs["title"],
                        start_semester=self._semesters.get(start) if start else None,
                        end_semester=self._semesters.get(end) if end else None,
                        course_type=str(attrs.get("course-type") or ""),
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                self._skipped(item, exc)
        return result

    def folders(self, course_id: str) -> list[Folder]:
        result = []
        for item, _ in self._paginate(f"/courses/{course_id}/folders"):
            try:
                attrs = item.get("attributes") or {}
                parent = _rel_id(_dict(item.get("relationships")), "parent")
                result.append(
                    Folder(
                        id=item["id"],
                        name=attrs.get("name") or "",
                        parent_id=parent,
                        folder_type=attrs.get("folder-type") or "",
                    )
                )
            except (KeyError, TypeError, AttributeError) as exc:
                self._skipped(item, exc)
        return result

    def files(self, course_id: str) -> list[RemoteFile]:
        folders = {f.id: f for f in self.folders(course_id)}
        result = []
        for item, _ in self._paginate(f"/courses/{course_id}/file-refs"):
            try:
                attrs = item["attributes"]
                rel = _dict(item.get("relationships"))
                parent = _rel_id(rel, "parent")
                chdate = _parse_dt(attrs.get("chdate") or attrs.get("mkdate"))
                if chdate is None or not attrs.get("name"):
                    raise ValueError("missing name or date")
                result.append(
                    RemoteFile(
                        id=item["id"],
                        course_id=course_id,
                        name=attrs["name"],
                        folder_path=folder_path(folders, parent),
                        size=int(attrs.get("filesize") or 0),
                        chdate=chdate,
                        mime_type=attrs.get("mime-type") or "",
                        description=attrs.get("description") or "",
                        downloadable=bool(attrs.get("is-downloadable", True)),
                        content_id=_rel_id(rel, "file") or "",
                        folder_type=folders[parent].folder_type if parent in folders else "",
                    )
                )
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                self._skipped(item, exc)
        return result

    def courseware_files(self, course_id: str) -> list[RemoteFile]:
        """Files attached to Courseware blocks of a course (download, video, audio, ...).

        Uses ``include`` to load all pages, containers and blocks of a unit in two
        requests; only the file metadata needs one request per file.
        """
        try:
            units = self._get_json(f"/courses/{course_id}/courseware-units")["data"]
        except AuthFailed:
            raise
        except ApiError:
            return []
        file_ids: dict[str, str] = {}  # file-ref id -> page path
        for unit in units:
            root = _rel_id(unit.get("relationships") or {}, "structural-element")
            if not root:
                continue
            elements: dict[str, dict] = {}
            included: list[dict] = []
            doc = self._get_json(
                f"/courseware-structural-elements/{root}", {"include": "containers.blocks"}
            )
            elements[root] = doc["data"]
            included += doc.get("included") or []
            for item, inc in self._paginate(
                f"/courseware-structural-elements/{root}/descendants",
                {"include": "containers.blocks"},
            ):
                elements[item["id"]] = item
                included += inc
            containers = {x["id"]: x for x in included if x.get("type") == "courseware-containers"}
            for block in (x for x in included if x.get("type") == "courseware-blocks"):
                container = containers.get(_rel_id(block.get("relationships") or {}, "container"))
                element_id = _rel_id(
                    (container or {}).get("relationships") or {}, "structural-element"
                )
                page = _page_path(elements, element_id, root)
                for fid in _payload_file_ids((block.get("attributes") or {}).get("payload")):
                    file_ids.setdefault(fid, page)
        result = []
        for fid, page in file_ids.items():
            try:
                item = self._get_json(f"/file-refs/{fid}")["data"]
                attrs = item["attributes"]
                chdate = _parse_dt(attrs.get("chdate") or attrs.get("mkdate"))
                if chdate is None or not attrs.get("name"):
                    raise ValueError("missing name or date")
                result.append(
                    RemoteFile(
                        id=item["id"],
                        course_id=course_id,
                        name=attrs["name"],
                        folder_path=f"Courseware/{page}".rstrip("/"),
                        size=int(attrs.get("filesize") or 0),
                        chdate=chdate,
                        mime_type=attrs.get("mime-type") or "",
                        description=attrs.get("description") or "",
                        downloadable=bool(attrs.get("is-downloadable", True)),
                        content_id=_rel_id(_dict(item.get("relationships")), "file") or "",
                        folder_type="Courseware",
                    )
                )
            except AuthFailed:
                raise
            except ApiError as exc:
                self.warnings.append(f"courseware file {fid}: {exc}")
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                self._skipped({"id": fid}, exc)
        return result

    def courseware_units(self, course_id: str) -> int:
        """Number of Courseware units (content there is not synced)."""
        try:
            doc = self._get_json(f"/courses/{course_id}/courseware-units", {"page[limit]": 1})
        except AuthFailed:
            raise
        except ApiError:
            return 0
        total = _dict(_dict(doc.get("meta")).get("page")).get("total")
        return int(total) if total is not None else len(doc.get("data") or [])

    def _skipped(self, item: Any, exc: Exception) -> None:
        item_id = item.get("id", "?") if isinstance(item, dict) else "?"
        message = f"skipped unexpected API record {item_id}: {exc!r}"
        log.warning(message)
        self.warnings.append(message)

    def download(self, file_id: str, sink: Callable[[bytes], None], chunk: int = 1 << 16) -> None:
        """Stream a file's content into ``sink`` chunk by chunk."""
        response = self._request("GET", f"{self.api_url}/file-refs/{file_id}/content", stream=True)
        try:
            for block in response.iter_bytes(chunk):
                sink(block)
        finally:
            response.close()


def _payload_file_ids(payload: Any) -> list[str]:
    """File-ref ids referenced by a Courseware block payload."""
    if not isinstance(payload, dict):
        return []
    ids: list[str] = []
    for key, value in payload.items():
        if key in ("file_id", "file_ref_id") and isinstance(value, str) and value:
            ids.append(value)
        elif key in ("file_ids", "files") and isinstance(value, list):
            ids += [
                v if isinstance(v, str) else v.get("id", "")
                for v in value
                if isinstance(v, (str, dict))
            ]
    return [i for i in ids if i]


def _page_path(elements: dict[str, dict], element_id: str | None, root: str) -> str:
    """Titles from the unit's first level down to the page, e.g. "Setup/Install"."""
    parts: list[str] = []
    seen: set[str] = set()
    while element_id and element_id in elements and element_id != root and element_id not in seen:
        seen.add(element_id)
        element = elements[element_id]
        parts.append(((element.get("attributes") or {}).get("title") or "").replace("/", "-"))
        element_id = _rel_id(element.get("relationships") or {}, "parent")
    return "/".join(reversed([p for p in parts if p]))


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
