# SPDX-FileCopyrightText: 2026 Asdao
# SPDX-License-Identifier: MIT

"""Enforce repository-level author attribution.

Checks that LICENSE, CITATION.cff, AUTHORS.md, and frontend/package.json
all correctly identify the project author.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


AUTHOR = "Asdao"

def check_repository_attribution() -> list[str]:
    failures = []

    license_text = Path("LICENSE").read_text(encoding="utf-8")
    expected_license_notice = "Copyright (c) 2026 Asdao"
    if expected_license_notice not in license_text:
        failures.append(f"LICENSE must contain {expected_license_notice!r}")

    package_json_path = Path("frontend/package.json")
    if package_json_path.exists():
        package_json = json.loads(package_json_path.read_text(encoding="utf-8"))
        if package_json.get("author") != AUTHOR:
            failures.append('frontend/package.json must contain "author": "Asdao"')

    citation_path = Path("CITATION.cff")
    if not citation_path.exists():
        failures.append("CITATION.cff is required for repository author attribution")
    else:
        citation_text = citation_path.read_text(encoding="utf-8")
        if "name: Asdao" not in citation_text:
            failures.append("CITATION.cff must include author name: Asdao")

    authors_path = Path("AUTHORS.md")
    if not authors_path.exists():
        failures.append("AUTHORS.md is required for repository author attribution")
    else:
        authors_text = authors_path.read_text(encoding="utf-8")
        if "Asdao" not in authors_text or "Copyright (c) 2026 Asdao" not in authors_text:
            failures.append("AUTHORS.md must identify Asdao and the 2026 copyright notice")

    return failures


def main() -> int:
    failures = check_repository_attribution()

    if failures:
        print("Author attribution CI failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("Author attribution CI passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
