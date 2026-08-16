# SPDX-FileCopyrightText: 2026 Asdao
# SPDX-License-Identifier: MIT

"""Gate `npm audit` results, with an explicit allowlist for advisories that
cannot be acted on.

`npm audit --audit-level=high` has no equivalent of pip-audit's --ignore-vuln,
so an advisory with no published fix makes the job permanently red and the
whole gate stops carrying information. This script keeps the gate meaningful:
every high or critical advisory fails the build unless it is listed in
ALLOWED_ADVISORIES below with a reason and a condition for removing it.

Usage:
    python scripts/check_npm_audit.py [--audit-level high] [--dir frontend]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys


# Advisory URL -> why it is accepted, and when the entry should be deleted.
ALLOWED_ADVISORIES: dict[str, str] = {
    "https://github.com/advisories/GHSA-f88m-g3jw-g9cj": (
        "sharp <0.35.0 inherits libvips CVE-2026-33327/33328/35590/35591. "
        "No fix is published, and sharp is not reachable in the shipped "
        "artifact: it is a Node-only native addon that @huggingface/"
        "transformers declares for server-side image decoding, while Obital "
        "uses transformers.js exclusively in the browser worker "
        "(frontend/src/workers/embedding.worker.ts). The production bundle in "
        "backend/app/templates/assets/ contains no reference to libvips or to "
        "any sharp platform binding. Remove this entry once sharp publishes "
        "0.35.0 or later and @huggingface/transformers widens its range."
    ),
}

SEVERITY_ORDER = ["info", "low", "moderate", "high", "critical"]


def collect_advisories(report: dict) -> list[dict]:
    """Flatten npm's per-package tree into concrete advisories.

    A package whose `via` contains only strings is affected purely through a
    dependency, so it carries no advisory of its own and is covered once the
    upstream advisory is resolved or allowlisted.
    """
    seen: dict[str, dict] = {}
    for pkg, entry in report.get("vulnerabilities", {}).items():
        for via in entry.get("via", []):
            if not isinstance(via, dict):
                continue
            url = via.get("url", "")
            seen.setdefault(
                url,
                {
                    "url": url,
                    "package": via.get("name", pkg),
                    "severity": via.get("severity", "unknown"),
                    "title": via.get("title", ""),
                },
            )
    return list(seen.values())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-level", default="high", choices=SEVERITY_ORDER)
    parser.add_argument("--dir", default="frontend")
    args = parser.parse_args()

    threshold = SEVERITY_ORDER.index(args.audit_level)

    # npm audit exits non-zero when it finds anything; the JSON is still valid.
    proc = subprocess.run(
        ["npm", "audit", "--json"],
        cwd=args.dir,
        capture_output=True,
        text=True,
    )
    if not proc.stdout.strip():
        print("npm audit produced no output:")
        print(proc.stderr.strip())
        return 1

    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        print(f"could not parse npm audit output: {exc}")
        return 1

    advisories = collect_advisories(report)
    blocking, allowed = [], []
    for adv in advisories:
        severity = adv["severity"]
        if severity not in SEVERITY_ORDER or SEVERITY_ORDER.index(severity) < threshold:
            continue
        (allowed if adv["url"] in ALLOWED_ADVISORIES else blocking).append(adv)

    for adv in allowed:
        print(f"ALLOWED  {adv['severity']:8} {adv['package']} - {adv['url']}")
        print(f"         {ALLOWED_ADVISORIES[adv['url']].split('.')[0]}.")

    if blocking:
        print(f"\nnpm audit failed: {len(blocking)} unaddressed advisory(ies) "
              f"at {args.audit_level} or above:")
        for adv in blocking:
            print(f"- {adv['severity']:8} {adv['package']}: {adv['title']}")
            print(f"  {adv['url']}")
        print("\nFix with `npm audit fix`, or add a justified entry to "
              "ALLOWED_ADVISORIES in this script.")
        return 1

    totals = report.get("metadata", {}).get("vulnerabilities", {})
    below = ", ".join(
        f"{n} {s}"
        for s, n in totals.items()
        if n and s in SEVERITY_ORDER and SEVERITY_ORDER.index(s) < threshold
    )
    print(
        f"\nnpm audit passed at level '{args.audit_level}' "
        f"({len(allowed)} allowlisted)."
        + (f" Below threshold, not gated: {below}." if below else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
