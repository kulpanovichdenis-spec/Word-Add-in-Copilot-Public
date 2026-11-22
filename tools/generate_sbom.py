"""Сформировать минимальный CycloneDX SBOM из установленной сборочной среды."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path

from packaging.utils import canonicalize_name


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--name", default="word-addin-copilot-public")
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    components = []
    for dist in sorted(importlib.metadata.distributions(), key=lambda item: canonicalize_name(item.metadata["Name"])):
        name = dist.metadata["Name"]
        if canonicalize_name(name) == "word-addin-copilot-public":
            continue
        component = {
            "type": "library",
            "name": name,
            "version": dist.version,
            "purl": f"pkg:pypi/{canonicalize_name(name)}@{dist.version}",
        }
        license_name = dist.metadata.get("License-Expression") or dist.metadata.get("License")
        if license_name and license_name.strip() and license_name.strip().upper() != "UNKNOWN":
            component["licenses"] = [{"license": {"name": license_name.strip()[:500]}}]
        components.append(component)

    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "metadata": {"component": {"type": "application", "name": args.name, "version": args.version}},
        "components": components,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
