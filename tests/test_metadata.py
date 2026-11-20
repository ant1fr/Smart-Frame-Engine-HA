"""File-level checks that hassfest/HACS would otherwise only catch in CI."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

import pytest
import yaml

from custom_components.smart_frame_engine import services
from custom_components.smart_frame_engine.binary_sensor import BINARY_SENSORS
from custom_components.smart_frame_engine.button import BUTTONS
from custom_components.smart_frame_engine.image import CURRENT_IMAGE
from custom_components.smart_frame_engine.sensor import REFRESH_RULE_SOURCES, SENSORS
from custom_components.smart_frame_engine.switch import FAMILY_MODE

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


# -- Services, entities and exceptions are fully translated ----------------------------


def _english() -> dict[str, Any]:
    return _load(INTEGRATION / "translations" / "en.json")


def test_services_yaml_matches_schemas_and_translations() -> None:
    """Every service/field in services.yaml has a schema key and an English name+description."""
    definitions = yaml.safe_load((INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
    schemas = {
        services.SERVICE_SET_AMBIENT_CONTEXT: services.SET_AMBIENT_CONTEXT_SCHEMA,
        services.SERVICE_CLEAR_AMBIENT_CONTEXT: services.CLEAR_AMBIENT_CONTEXT_SCHEMA,
        services.SERVICE_SEND_TITLE_CARD: services.SEND_TITLE_CARD_SCHEMA,
        services.SERVICE_SHOW_NOW_PLAYING: services.SHOW_NOW_PLAYING_SCHEMA,
        services.SERVICE_SHOW_MEDIA_POSTER: services.SHOW_MEDIA_POSTER_SCHEMA,
    }
    translated = _english()["services"]

    assert set(definitions) == set(schemas) == set(translated)
    for name, definition in definitions.items():
        fields = set(definition["fields"])
        assert fields == {str(key) for key in schemas[name].schema}, name
        assert fields == set(translated[name]["fields"]), name
        assert translated[name]["name"]
        assert translated[name]["description"]
        for field, text in translated[name]["fields"].items():
            assert text["name"], (name, field)
            assert text["description"], (name, field)


def test_select_selectors_are_translated() -> None:
    """Each translation_key used by a select selector lists exactly its options."""
    definitions = yaml.safe_load((INTEGRATION / "services.yaml").read_text(encoding="utf-8"))
    selectors = _english()["selector"]
    seen = 0
    for definition in definitions.values():
        for field in definition["fields"].values():
            select = field["selector"].get("select")
            if select:
                seen += 1
                assert set(select["options"]) == set(
                    selectors[select["translation_key"]]["options"]
                )
    assert seen == 5


def test_every_entity_has_a_translated_name() -> None:
    """Entity names come from translations: every translation_key must exist."""
    entities = _english()["entity"]
    expected = {
        "binary_sensor": BINARY_SENSORS,
        "button": BUTTONS,
        "image": (CURRENT_IMAGE,),
        "sensor": SENSORS,
        "switch": (FAMILY_MODE,),
    }
    assert set(entities) == set(expected)
    for platform, descriptions in expected.items():
        keys = {d.translation_key for d in descriptions}
        assert keys == {d.key for d in descriptions}, platform
        assert keys == set(entities[platform]), platform
    assert set(entities["sensor"]["refresh_rule"]["state"]) == set(REFRESH_RULE_SOURCES)


def test_every_translation_key_used_in_code_exists() -> None:
    """Exceptions and issues raised with a translation_key must be translated."""
    english = _english()
    known = set(english["exceptions"]) | set(english["issues"])
    used: set[str] = set()
    for path in INTEGRATION.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'translation_key="(\w+)"', text))
        used |= set(re.findall(r'translation_key=\(\s*"(\w+)"', text))
    # Keys chosen at runtime in coordinator.py (``"a" if x else "b"``) are listed explicitly.
    used |= {"incompatible_server", "incompatible_integration"}
    entity_keys = {k for platform in english["entity"].values() for k in platform}
    assert used - entity_keys <= known, sorted(used - entity_keys - known)


@pytest.mark.parametrize("name", ["strings.json", "translations/en.json", "translations/fr.json"])
def test_translations_contain_no_urls(name: str) -> None:
    """hassfest rejects a literal URL in a translation string (use a placeholder instead)."""
    offenders = {
        key: text
        for key, text in _leaves(_load(INTEGRATION / name)).items()
        if isinstance(text, str) and re.search(r"https?://|www\.", text)
    }
    assert not offenders
