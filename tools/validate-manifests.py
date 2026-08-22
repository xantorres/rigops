#!/usr/bin/env python3
"""Validate plugin marketplace manifests without the claude CLI.

CI backstop for .claude-plugin/marketplace.json and each referenced
plugin's plugin.json.
"""

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
MARKETPLACE_PATH = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def load_json(path, errors):
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"error: cannot read {path}: {exc}")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        errors.append(f"error: invalid JSON in {path}: {exc}")
        return None


def check_str(value, field, path, errors):
    if not isinstance(value, str):
        errors.append(f"error: {path}: {field} must be a string")
        return False
    return True


def validate_plugin_entry(entry, index, errors):
    label = f"plugins[{index}]"
    if not isinstance(entry, dict):
        errors.append(f"error: marketplace.json: {label} must be an object")
        return None
    name = entry.get("name")
    source = entry.get("source")
    description = entry.get("description")
    version = entry.get("version")
    ok = True
    ok &= check_str(name, f"{label}.name", MARKETPLACE_PATH, errors)
    ok &= check_str(source, f"{label}.source", MARKETPLACE_PATH, errors)
    ok &= check_str(description, f"{label}.description", MARKETPLACE_PATH, errors)
    if not ok:
        return None
    return {"name": name, "source": source, "version": version}


def validate_plugin_dir(entry, errors):
    name = entry["name"]
    source = entry["source"]
    version = entry.get("version")
    if not source.startswith("./"):
        return

    plugin_dir = REPO_ROOT / source
    if not plugin_dir.is_dir():
        errors.append(f"error: plugin '{name}': source directory not found: {source}")
        return

    plugin_json_path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not plugin_json_path.exists():
        errors.append(f"error: plugin '{name}': plugin.json not found: {plugin_json_path}")
        return

    plugin_json = load_json(plugin_json_path, errors)
    if plugin_json is None:
        return
    if not isinstance(plugin_json, dict):
        errors.append(f"error: {plugin_json_path}: must be a JSON object")
        return

    plugin_name = plugin_json.get("name")
    if plugin_name != name:
        errors.append(
            f"error: {plugin_json_path}: name '{plugin_name}' does not match "
            f"marketplace entry name '{name}'"
        )

    if "version" not in plugin_json:
        errors.append(f"error: {plugin_json_path}: missing required field 'version'")
    elif version is not None and plugin_json.get("version") != version:
        errors.append(
            f"error: {plugin_json_path}: version '{plugin_json.get('version')}' does not match "
            f"marketplace entry version '{version}'"
        )

    if "hooks" in plugin_json:
        errors.append(
            f'error: {plugin_json_path}: plugin.json must not declare "hooks"; '
            "hooks/hooks.json is auto-loaded"
        )


def main():
    errors = []

    if not MARKETPLACE_PATH.exists():
        print(f"error: marketplace manifest not found: {MARKETPLACE_PATH}")
        return 1

    marketplace = load_json(MARKETPLACE_PATH, errors)
    if marketplace is None:
        for err in errors:
            print(err)
        return 1

    if not isinstance(marketplace, dict):
        errors.append(f"error: {MARKETPLACE_PATH}: must be a JSON object")
        for err in errors:
            print(err)
        return 1

    check_str(marketplace.get("name"), "name", MARKETPLACE_PATH, errors)

    owner = marketplace.get("owner")
    if not isinstance(owner, dict):
        errors.append(f"error: {MARKETPLACE_PATH}: owner must be an object")
    else:
        check_str(owner.get("name"), "owner.name", MARKETPLACE_PATH, errors)

    plugins = marketplace.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        errors.append(f"error: {MARKETPLACE_PATH}: plugins must be a non-empty list")
        plugins = []

    valid_count = 0
    for index, entry in enumerate(plugins):
        validated = validate_plugin_entry(entry, index, errors)
        if validated is None:
            continue
        validate_plugin_dir(validated, errors)
        valid_count += 1

    if errors:
        for err in errors:
            print(err)
        return 1

    print(f"validate-manifests: OK ({valid_count} plugin(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
