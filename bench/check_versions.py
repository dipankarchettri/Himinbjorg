"""The agent and the extension are released together, under one tag, so
they carry one version. Run by CI on every push and by the release
workflow before it builds; exits 1 when they disagree."""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def versions() -> dict[str, str]:
    lock = json.loads((ROOT / "extension" / "package-lock.json").read_text())
    return {
        "pyproject.toml": tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"],
        "extension/package.json": json.loads((ROOT / "extension" / "package.json").read_text())["version"],
        "extension/package-lock.json": lock["version"],
        "extension/package-lock.json (root package)": lock["packages"][""]["version"],
    }


def main() -> int:
    found = versions()
    if len(set(found.values())) == 1:
        print(f"version {next(iter(found.values()))} everywhere")
        return 0
    for where, v in found.items():
        print(f"  {v:10} {where}", file=sys.stderr)
    print("versions disagree; set them all to the same value", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
