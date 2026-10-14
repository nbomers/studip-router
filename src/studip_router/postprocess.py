"""Steps after downloading: extracting ZIP archives, unlocking and merging PDFs."""

from __future__ import annotations

import hashlib
import io
import os
import re
import shutil
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from .fsutil import atomic_writer, is_ignored, local_stat, nfc, sha256_file, split_ext

MAX_UNZIPPED_BYTES = 4 * 1024**3
MAX_MEMBERS = 20_000


class PostprocessError(Exception):
    pass


# -- unzip ------------------------------------------------------------------------


def unzip_target(zip_path: Path) -> Path:
    return zip_path.with_name(split_ext(zip_path.name)[0])


def extract_zip(zip_path: Path) -> Path | None:
    """Extract next to the archive into a folder named like it.

    Returns the folder, or None if it already exists (never merges into or
    overwrites existing content). Extraction happens in a temporary folder that
    is renamed at the end, so an interrupted run leaves nothing half-extracted.
    """
    target = unzip_target(zip_path)
    if target.exists():
        return None
    tmp = target.with_name(target.name + ".unzip-tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            members = [m for m in archive.infolist() if not m.is_dir()]
            if len(members) > MAX_MEMBERS:
                raise PostprocessError(f"{zip_path.name}: too many entries ({len(members)})")
            if sum(m.file_size for m in members) > MAX_UNZIPPED_BYTES:
                raise PostprocessError(f"{zip_path.name}: archive too large when extracted")
            for member in members:
                rel = _safe_member_path(member_name(member))
                if rel is None:
                    continue
                dest = tmp.joinpath(*rel.parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out, 1 << 20)
        tmp.mkdir(exist_ok=True)  # empty archives still yield a folder
        os.replace(tmp, target)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return target


def member_name(info: zipfile.ZipInfo) -> str:
    """Archive member name; repairs UTF-8 names stored without the UTF-8 flag.

    Python decodes unflagged names as cp437, which garbles UTF-8 names written
    by some tools ("Übung" becomes "├£bung").
    """
    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            return name.encode("cp437").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _safe_member_path(name: str) -> PurePosixPath | None:
    """Reject absolute paths and '..' (zip slip); drop macOS metadata."""
    path = PurePosixPath(nfc(name.replace("\\", "/")))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise PostprocessError(f"unsafe path in archive: {name!r}")
    if path.parts[0] == "__MACOSX" or is_ignored(path.name):
        return None
    return path


# -- unlock PDFs ------------------------------------------------------------------


class Unlock(StrEnum):
    DECRYPTED = "decrypted"
    NOT_PDF = "not a PDF"
    NOT_ENCRYPTED = "not encrypted"
    OPENS_WITHOUT_PASSWORD = "opens without a password"  # only print/copy restrictions
    WRONG_PASSWORD = "the course PDF password does not fit"


def is_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return b"%PDF-" in fh.read(1024)
    except OSError:
        return False


def unlock_pdf(path: Path, password: str) -> Unlock:
    """Remove the open password from ``path`` in place, if ``password`` fits.

    Everything else is left untouched byte for byte: other files, PDFs without
    encryption, PDFs that open without a password (permission flags only) and
    PDFs that need a different password.
    """
    if not is_pdf(path):
        return Unlock.NOT_PDF
    from pypdf import PasswordType, PdfReader, PdfWriter  # lazily: only for this feature
    from pypdf.errors import DependencyError, PyPdfError

    try:
        reader = PdfReader(path)
        if not reader.is_encrypted:
            return Unlock.NOT_ENCRYPTED
        if reader.decrypt("") != PasswordType.NOT_DECRYPTED:
            return Unlock.OPENS_WITHOUT_PASSWORD
        if reader.decrypt(password) == PasswordType.NOT_DECRYPTED:
            return Unlock.WRONG_PASSWORD
        buffer = io.BytesIO()  # pypdf needs a seekable stream
        PdfWriter(clone_from=reader).write(buffer)
        with atomic_writer(path) as (fh, _):
            fh.write(buffer.getvalue())
    except (PyPdfError, DependencyError, ValueError, KeyError) as exc:  # broken PDF, no AES
        raise PostprocessError(f"{path.name}: could not unlock: {exc}") from exc
    return Unlock.DECRYPTED


# -- merge slides ------------------------------------------------------------------

_VERSION = re.compile(r"_v(\d+)$")


@dataclass(frozen=True)
class MergeResult:
    output: Path
    inputs: list[Path]
    written: bool
    reason: str = ""


def _natural_key(name: str) -> list[object]:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", nfc(name).casefold())]


def merge_inputs(source: Path, output_name: str) -> list[Path]:
    """PDFs in ``source``, natural order, only the newest ``_vN`` of each."""
    newest: dict[str, tuple[int, Path]] = {}
    for entry in source.iterdir() if source.is_dir() else ():
        if not entry.is_file() or entry.suffix.lower() != ".pdf" or is_ignored(entry.name):
            continue
        if nfc(entry.name).casefold() == nfc(output_name).casefold():
            continue
        stem = split_ext(entry.name)[0]
        m = _VERSION.search(stem)
        base, version = (stem[: m.start()], int(m.group(1))) if m else (stem, 1)
        key = nfc(base).casefold()
        if key not in newest or newest[key][0] < version:
            newest[key] = (version, entry)
    return sorted((p for _, p in newest.values()), key=lambda p: _natural_key(p.name))


def inputs_fingerprint(inputs: list[Path]) -> str:
    digest = hashlib.sha256()
    for p in inputs:
        st = p.stat()
        digest.update(f"{nfc(p.name)}\0{st.st_size}\0{int(st.st_mtime)}\n".encode())
    return digest.hexdigest()


def merge_pdfs(
    source: Path,
    output: Path,
    *,
    previous_sha: str | None,
    previous_fingerprint: str | None,
    allow_icloud_download: bool = False,
) -> tuple[MergeResult, str | None, str | None]:
    """Merge PDFs; returns (result, new output sha, new inputs fingerprint).

    Never overwrites an output file that was changed by someone else (its hash
    differs from what we wrote last time) or a file we did not create.
    """
    from pypdf import PdfWriter  # imported lazily: only needed for this feature

    inputs = merge_inputs(source, output.name)
    if not inputs:
        return MergeResult(output, [], False, "no PDFs found"), previous_sha, previous_fingerprint
    fingerprint = inputs_fingerprint(inputs)
    if fingerprint == previous_fingerprint and output.exists():
        return MergeResult(output, inputs, False, "up to date"), previous_sha, fingerprint
    if output.exists():
        if previous_sha is None:
            return (
                MergeResult(
                    output, inputs, False, "output exists but was not created by studip-router"
                ),
                previous_sha,
                previous_fingerprint,
            )
        st = local_stat(output)
        if st and not st.dataless and sha256_file(output) != previous_sha:
            return (
                MergeResult(output, inputs, False, "output was modified, not overwriting"),
                previous_sha,
                previous_fingerprint,
            )
    evicted = [p for p in inputs if (st := local_stat(p)) and st.dataless]
    if evicted and not allow_icloud_download:
        return (
            MergeResult(
                output,
                inputs,
                False,
                f"{len(evicted)} PDF(s) only in iCloud; use --force to download",
            ),
            previous_sha,
            previous_fingerprint,
        )

    writer = PdfWriter()
    for pdf in inputs:
        writer.append(str(pdf), outline_item=split_ext(pdf.name)[0])
    tmp = output.with_name(output.name + ".part")
    try:
        with tmp.open("wb") as fh:
            writer.write(fh)
        os.replace(tmp, output)
    finally:
        tmp.unlink(missing_ok=True)
    return MergeResult(output, inputs, True), sha256_file(output), fingerprint
