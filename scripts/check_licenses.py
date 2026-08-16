# SPDX-FileCopyrightText: 2026 Asdao
# SPDX-License-Identifier: MIT

"""Check dependency licenses and third-party attribution notices.

The checker intentionally focuses on direct dependencies listed in requirement
files and package.json. It is small enough to run in CI without relying on a
hosted license service.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from importlib import metadata
from pathlib import Path


DEFAULT_ALLOWED_LICENSES = {
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "ISC",
    "MIT",
    "MPL-2.0",
    "Python-2.0",
}

PYTHON_LICENSE_CLASSIFIERS = {
    "License :: OSI Approved :: Apache Software License": "Apache-2.0",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "Python-2.0",
}

REQUIREMENT_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)")
NPM_DEPENDENCY_SECTIONS = ("dependencies", "devDependencies", "optionalDependencies")


def normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_policy(policy_path: Path | None) -> tuple[set[str], dict[str, str]]:
    if not policy_path:
        return set(DEFAULT_ALLOWED_LICENSES), {}
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    reviewed = {
        normalize_name(package_name): license_name
        for package_name, license_name in (policy.get("package_licenses") or {}).items()
    }
    return set(policy.get("allowed_licenses", DEFAULT_ALLOWED_LICENSES)), reviewed


def parse_requirement_names(path: Path) -> list[str]:
    names: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith(("-r", "--")):
            continue
        match = REQUIREMENT_RE.match(line)
        if match:
            names.append(match.group(1))
    return names


def python_license_for(distribution_name: str) -> tuple[str | None, bool]:
    try:
        dist = metadata.distribution(distribution_name)
    except metadata.PackageNotFoundError:
        return None, False

    classifiers = dist.metadata.get_all("Classifier") or []
    for classifier in classifiers:
        if classifier in PYTHON_LICENSE_CLASSIFIERS:
            return PYTHON_LICENSE_CLASSIFIERS[classifier]

    license_expression = (dist.metadata.get("License-Expression") or "").strip()
    if license_expression:
        return license_expression, True

    license_text = (dist.metadata.get("License") or "").strip()
    if not license_text or license_text.upper() == "UNKNOWN":
        return None, True
    return license_text, True


def npm_dependencies(package_json: Path) -> list[str]:
    data = json.loads(package_json.read_text(encoding="utf-8"))
    names: list[str] = []
    for section in NPM_DEPENDENCY_SECTIONS:
        names.extend((data.get(section) or {}).keys())
    return names


def npm_license_for(package_json: Path, package_name: str) -> str | None:
    package_lock = package_json.parent / "package-lock.json"
    if package_lock.exists():
        lock_data = json.loads(package_lock.read_text(encoding="utf-8"))
        package_entry = (lock_data.get("packages") or {}).get(f"node_modules/{package_name}") or {}
        lock_license = package_entry.get("license")
        if isinstance(lock_license, str):
            return lock_license

    package_root = package_json.parent / "node_modules" / package_name / "package.json"
    if not package_root.exists():
        return None
    data = json.loads(package_root.read_text(encoding="utf-8"))
    license_value = data.get("license")
    if isinstance(license_value, str):
        return license_value
    if isinstance(license_value, dict) and isinstance(license_value.get("type"), str):
        return license_value["type"]
    return None


def check_notice(notice_path: Path, package_names: list[str]) -> list[str]:
    if not notice_path.exists():
        return [f"Missing attribution file: {notice_path}"]

    notice_text = notice_path.read_text(encoding="utf-8").lower()
    missing = []
    for name in sorted({normalize_name(package_name) for package_name in package_names}):
        if name not in notice_text:
            missing.append(f"{name} is missing from {notice_path}")
    return missing


def validate_license(
    kind: str,
    package_name: str,
    license_name: str | None,
    allowed: set[str],
    installed: bool = True,
) -> str | None:
    if not installed:
        return f"{kind} dependency {package_name!r} is not installed, so license metadata could not be checked"
    if not license_name:
        return f"{kind} dependency {package_name!r} has no discoverable license metadata"
    if license_name not in allowed:
        return f"{kind} dependency {package_name!r} uses license {license_name!r}, which is not allowed"
    return None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--notice", type=Path, default=Path("THIRD_PARTY_NOTICES.md"))
    parser.add_argument("--python-requirements", type=Path, action="append", default=[])
    parser.add_argument("--npm-package-json", type=Path, action="append", default=[])
    args = parser.parse_args(argv)

    allowed, reviewed_licenses = read_policy(args.policy)
    failures: set[str] = set()
    attributed_packages: set[str] = set()

    for requirements_file in args.python_requirements:
        for package_name in parse_requirement_names(requirements_file):
            attributed_packages.add(package_name)
            license_name = reviewed_licenses.get(normalize_name(package_name))
            installed = True
            if not license_name:
                license_name, installed = python_license_for(package_name)
            failure = validate_license("Python", package_name, license_name, allowed, installed)
            if failure:
                failures.add(failure)

    for package_json in args.npm_package_json:
        for package_name in npm_dependencies(package_json):
            attributed_packages.add(package_name)
            license_name = reviewed_licenses.get(normalize_name(package_name)) or npm_license_for(
                package_json, package_name
            )
            failure = validate_license("npm", package_name, license_name, allowed)
            if failure:
                failures.add(failure)

    failures.update(check_notice(args.notice, sorted(attributed_packages)))

    if failures:
        print("License CI failed:")
        for failure in sorted(failures):
            print(f"- {failure}")
        return 1

    print(f"License CI passed for {len(set(map(normalize_name, attributed_packages)))} direct dependencies.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
