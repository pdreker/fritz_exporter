#!/usr/bin/env python3
"""Notify (via a GitHub issue) when a newer *stable* Python minor/major exists.

Dependabot is configured to ignore minor/major bumps of the python base image
(see .github/dependabot.yml) because its prerelease detection fails on the
"-alpine" variant tag and would otherwise propose betas/RCs. That ignore also
suppresses the useful heads-up for new *stable* releases, so this script
restores it: it compares the Python version pinned in the Dockerfile against
the newest released stable cycle from endoflife.date (which never lists
prereleases) and opens a tracking issue when a newer minor/major is available.

Patch updates within the current minor still flow through Dependabot.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ENDOFLIFE_URL = "https://endoflife.date/api/python.json"
DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"
ISSUE_LABEL = "python-upgrade"


def current_minor() -> tuple[int, int]:
    text = DOCKERFILE.read_text()
    m = re.search(r"^FROM python:(\d+)\.(\d+)\.\d+-alpine", text, re.MULTILINE)
    if not m:
        sys.exit(f"Could not find 'FROM python:X.Y.Z-alpine' in {DOCKERFILE}")
    return int(m.group(1)), int(m.group(2))


def newest_stable_cycle() -> tuple[tuple[int, int], str]:
    with urllib.request.urlopen(ENDOFLIFE_URL, timeout=30) as resp:
        cycles = json.load(resp)
    best_key: tuple[int, int] | None = None
    best_latest = ""
    for c in cycles:
        # Skip cycles whose latest is a prerelease (defensive; endoflife.date
        # normally only lists released, stable versions).
        latest = str(c.get("latest", ""))
        if not re.fullmatch(r"\d+\.\d+\.\d+", latest):
            continue
        maj, _, minor = c["cycle"].partition(".")
        key = (int(maj), int(minor))
        if best_key is None or key > best_key:
            best_key, best_latest = key, latest
    if best_key is None:
        sys.exit("No stable Python cycle found in endoflife.date data")
    return best_key, best_latest


def issue_exists(title: str) -> bool:
    out = subprocess.run(
        ["gh", "issue", "list", "--state", "open", "--search", title, "--json", "title"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return any(i.get("title") == title for i in json.loads(out or "[]"))


def main() -> None:
    cur = current_minor()
    newest, latest_patch = newest_stable_cycle()
    if newest <= cur:
        print(f"Up to date: running {cur[0]}.{cur[1]}, newest stable is {newest[0]}.{newest[1]}")
        return

    version = f"{newest[0]}.{newest[1]}"
    title = f"New stable Python release available: {version}"
    if issue_exists(title):
        print(f"Issue already open: {title}")
        return

    body = (
        f"A newer stable Python release is available.\n\n"
        f"- Currently pinned in `Dockerfile`: **{cur[0]}.{cur[1]}.x**\n"
        f"- Newest stable release: **{latest_patch}** (cycle {version})\n\n"
        f"Dependabot intentionally does not propose minor/major Python bumps "
        f"(see `.github/dependabot.yml`), so update the `FROM python:...-alpine` "
        f"line in the `Dockerfile` manually when ready.\n\n"
        f"_Opened automatically by `.github/workflows/python-release-check.yaml`._"
    )
    create = ["gh", "issue", "create", "--title", title, "--body", body]
    # Use the label if it exists; otherwise fall back to an unlabelled issue
    # rather than failing the whole run.
    if subprocess.run([*create, "--label", ISSUE_LABEL]).returncode != 0:
        subprocess.run(create, check=True)
    print(f"Opened issue: {title}")


if __name__ == "__main__":
    main()
