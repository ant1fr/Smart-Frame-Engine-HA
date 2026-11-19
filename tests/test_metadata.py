"""File-level checks that hassfest/HACS would otherwise only catch in CI."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent
INTEGRATION = ROOT / "custom_components" / "smart_frame_engine"
PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _leaves(node: Any, prefix: str = "") -> dict[str, str]:
    if isinstance(node, dict):
        out: dict[str, str] = {}
        for key, value in node.items():
            out.update(_leaves(value, f"{prefix}.{key}" if prefix else key))
        return out
    return {prefix: node}


def test_manifest_key_order_and_values() -> None:
    """hassfest: domain, name, then every other key sorted alphabetically."""
    manifest = _load(INTEGRATION / "manifest.json")
    keys = list(manifest)
    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])
    assert manifest["domain"] == "smart_frame_engine"
    assert manifest["iot_class"] == "local_polling"
    assert manifest["config_flow"] is True
    assert manifest["codeowners"] == ["@ant1fr"]
    assert manifest["version"] == "1.0.0"
    assert manifest["zeroconf"] == [{"type": "_smartframe._tcp.local."}]


def test_hacs_json() -> None:
    """HACS metadata."""
    assert _load(ROOT / "hacs.json") == {
        "name": "Smart Frame Engine",
        "render_readme": True,
        "homeassistant": "2026.1.0",
    }


def test_strings_and_english_translation_are_byte_identical() -> None:
    """strings.json and translations/en.json must not drift."""
    assert (INTEGRATION / "strings.json").read_bytes() == (
        INTEGRATION / "translations" / "en.json"
    ).read_bytes()


def test_no_unresolved_translation_references() -> None:
    """Custom integrations do not get ``[%key:...%]`` resolved: they would show literally."""
    assert "[%key:" not in (INTEGRATION / "translations" / "en.json").read_text(encoding="utf-8")


def test_french_translation_covers_everything() -> None:
    """fr.json has exactly the keys of en.json and the same placeholders in every string."""
    english = _leaves(_load(INTEGRATION / "translations" / "en.json"))
    french = _leaves(_load(INTEGRATION / "translations" / "fr.json"))

    assert set(french) == set(english)
    for key, text in english.items():
        assert french[key].strip(), key
        assert set(PLACEHOLDER.findall(french[key])) == set(PLACEHOLDER.findall(text)), key


@pytest.mark.parametrize("issue", ["integration_outdated", "server_outdated"])
def test_repair_issues_are_translated(issue: str) -> None:
    """Both Repair issues have a title and a description in both languages."""
    for lang in ("en", "fr"):
        data = _load(INTEGRATION / "translations" / f"{lang}.json")
        assert data["issues"][issue]["title"]
        assert data["issues"][issue]["description"]
