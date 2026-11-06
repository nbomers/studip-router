"""Write the Homebrew formula for a released version (for the tap nbomers/homebrew-tap).

    uv run python scripts/homebrew_formula.py 0.5.0 > studip-router.rb

The package itself comes from PyPI (its sdist URL and SHA-256 are fetched, so run
this after the release is on PyPI); every dependency is a ``resource`` with the
sdist pinned in ``uv.lock``. ``cryptography`` (and with it ``cffi``) is taken from
Homebrew instead of being built with Rust. Only the dependencies needed on macOS
and Linux with the formula's Python are included; markers are evaluated with
``packaging``.

Check the result with ``brew audit --strict --new studip-router`` and
``brew install --build-from-source ./studip-router.rb`` before pushing it.
"""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.request
from pathlib import Path

from packaging.markers import Marker

PYTHON = "3.13"
FROM_HOMEBREW = {"cryptography": "cryptography", "cffi": None, "pycparser": None}
LOCK = Path(__file__).resolve().parent.parent / "uv.lock"


def _applies(marker: str | None, platform: str) -> bool:
    if not marker:
        return True
    env = {"python_version": PYTHON, "python_full_version": PYTHON + ".0", "sys_platform": platform}
    return Marker(marker).evaluate(env)


def runtime_packages(lock: dict) -> list[dict]:
    packages = {p["name"]: p for p in lock["package"]}
    seen: set[str] = set()
    todo = [("studip-router", [])]
    while todo:
        name, extras = todo.pop()
        package = packages[name]
        deps = list(package.get("dependencies", []))
        for extra in extras:
            deps += package.get("optional-dependencies", {}).get(extra, [])
        for dep in deps:
            marker = dep.get("marker")
            if not (_applies(marker, "darwin") or _applies(marker, "linux")):
                continue
            todo.append((dep["name"], dep.get("extra", [])))
            seen.add(dep["name"])
    return [packages[n] for n in sorted(seen) if n not in FROM_HOMEBREW]


def linux_only(lock: dict) -> set[str]:
    """Packages only Linux needs (keyring's Secret Service backend)."""
    result = set()
    for package in lock["package"]:
        for dep in package.get("dependencies", []):
            marker = dep.get("marker")
            if marker and _applies(marker, "linux") and not _applies(marker, "darwin"):
                result.add(dep["name"])
    return result


def resource(package: dict) -> str:
    sdist = package.get("sdist")
    if not sdist:
        raise SystemExit(f"{package['name']}: no sdist in uv.lock")
    return (
        f'  resource "{package["name"]}" do\n'
        f'    url "{sdist["url"]}"\n'
        f'    sha256 "{sdist["hash"].removeprefix("sha256:")}"\n'
        "  end\n"
    )


def main(version: str) -> str:
    with urllib.request.urlopen(f"https://pypi.org/pypi/studip-router/{version}/json") as r:
        release = json.load(r)
    sdist = next(u for u in release["urls"] if u["packagetype"] == "sdist")
    lock = tomllib.loads(LOCK.read_text(encoding="utf-8"))
    linux = linux_only(lock)
    packages = runtime_packages(lock)
    common = "\n".join(resource(p) for p in packages if p["name"] not in linux)
    on_linux = "\n".join(
        "".join("    " + line[2:] for line in resource(p).splitlines(keepends=True))
        for p in packages
        if p["name"] in linux
    )
    linux_block = f"\n  on_linux do\n{on_linux}  end\n" if on_linux else ""
    return f'''class StudipRouter < Formula
  include Language::Python::Virtualenv

  desc "Sync Stud.IP course files into your own folders, with routing rules"
  homepage "https://github.com/nbomers/studip-router"
  url "{sdist["url"]}"
  sha256 "{sdist["digests"]["sha256"]}"
  license "MIT"

  depends_on "cryptography"
  depends_on "python@{PYTHON}"

{common}{linux_block}
  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match version.to_s, shell_output("#{{bin}}/studip --version")
  end
end
'''


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: homebrew_formula.py VERSION")
    print(main(sys.argv[1]), end="")
