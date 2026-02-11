#!/usr/bin/env python3
"""Validate KeriWasm runtime layout and PyScript mappings for CI."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PATHS = [
    "index.html",
    "pages/test-harness.html",
    "workers/liboqs_worker.js",
    "pyscript.toml",
]

SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
ATTR_RE = re.compile(r'([a-zA-Z_:][a-zA-Z0-9_:\-]*)\s*=\s*["\']([^"\']+)["\']')


def rel_to_root(path_str: str) -> Path:
    normalized = path_str.strip()
    if normalized.startswith("http://") or normalized.startswith("https://"):
        return Path("__external__")
    if normalized.startswith("/"):
        normalized = normalized[1:]
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return ROOT / normalized


def collect_script_refs(html_path: Path) -> tuple[list[str], list[str]]:
    py_srcs: list[str] = []
    py_configs: list[str] = []
    content = html_path.read_text(encoding="utf-8")

    for match in SCRIPT_TAG_RE.finditer(content):
        attrs = {k.lower(): v for k, v in ATTR_RE.findall(match.group(0))}
        if attrs.get("type", "").lower() != "py":
            continue

        src = attrs.get("src")
        if src:
            py_srcs.append(src)

        config = attrs.get("config")
        if config:
            py_configs.append(config)

    return py_srcs, py_configs


def main() -> int:
    missing: list[str] = []

    for rel in REQUIRED_PATHS:
        if not (ROOT / rel).exists():
            missing.append(rel)

    pyscript_path = ROOT / "pyscript.toml"
    if not pyscript_path.exists():
        print("Missing pyscript.toml")
        return 1

    cfg = tomllib.loads(pyscript_path.read_text(encoding="utf-8"))

    files_map = cfg.get("files", {})
    if not isinstance(files_map, dict):
        print("Invalid pyscript.toml: [files] must be a table")
        return 1

    packages = cfg.get("packages", [])
    if not isinstance(packages, list):
        print("Invalid pyscript.toml: packages must be a list")
        return 1

    missing_mapped_files = 0
    for src in files_map:
        source_path = rel_to_root(str(src))
        if not source_path.exists():
            missing.append(str(src))
            missing_mapped_files += 1

    missing_local_packages = 0
    for package in packages:
        if not isinstance(package, str):
            continue
        if package.startswith("./"):
            package_path = rel_to_root(package)
            if not package_path.exists():
                missing.append(package)
                missing_local_packages += 1

    html_refs_checked = 0
    for html_rel in ("index.html", "pages/test-harness.html"):
        html_path = ROOT / html_rel
        if not html_path.exists():
            continue
        srcs, configs = collect_script_refs(html_path)
        for src in srcs:
            src_path = rel_to_root(src)
            if src_path.name != "__external__" and not src_path.exists():
                missing.append(f"{html_rel}: {src}")
            html_refs_checked += 1

        for cfg_path in configs:
            config_path = rel_to_root(cfg_path)
            if config_path.name != "__external__" and not config_path.exists():
                missing.append(f"{html_rel}: {cfg_path}")

    if missing:
        print("Runtime layout check failed. Missing paths:")
        for path in sorted(set(missing)):
            print(f"- {path}")
        return 1

    print("Runtime layout check passed")
    print(f"Mapped files checked: {len(files_map)}")
    print(
        f"Local package files checked: {sum(1 for p in packages if isinstance(p, str) and p.startswith('./'))}"
    )
    print(f"PyScript script refs checked: {html_refs_checked}")
    print(f"Missing mapped files: {missing_mapped_files}")
    print(f"Missing local package files: {missing_local_packages}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
