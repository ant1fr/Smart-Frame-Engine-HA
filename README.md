# Smart Frame Engine for Home Assistant

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[Smart Frame Engine](https://github.com/ant1fr/Smart-Frame-Engine), installable through
[HACS](https://hacs.xyz/) or manually.

> **Status:** work in progress. Discovery, setup, authentication, compatibility checks, entities
> and services are in place; blueprints follow in a later milestone.

## Requirements

- Home Assistant 2026.1.0 or newer
- A Smart Frame Engine reachable from Home Assistant

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/ant1fr/Smart-Frame-Engine-HA` with category **Integration**.
3. Install **Smart Frame Engine** and restart Home Assistant.

### Manual

Copy `custom_components/smart_frame_engine` into the `custom_components` folder of your Home
Assistant configuration directory and restart Home Assistant.

## What you get

Everything is grouped under one device per engine. Entities that depend on an optional engine
feature (a *capability*) only appear when your engine supports it, and show up after an engine
update without any manual step.

| Platform | Entities |
| --- | --- |
| Image | **Current image**: live preview of what the frame displays |
| Sensor | Engine version, image source, refresh mode, media title (with the image metadata as attributes), ambient context summary, last delivery, frame last seen, frame battery, Wi-Fi signal (disabled by default) |
| Binary sensor | Engine connected, frame online, frame charging, image pending delivery, temporary image, quiet hours |
| Switch | **Family Mode** (needs the `family_mode` capability) |
| Button | **Refresh image** (`force_refresh`), **Wake up frame** (`frame_wakeup`) |

The *media title* sensor exposes the image's provider details (which may include people or places)
as an attribute that is shown in the UI but never stored in the history, and diagnostics redact it.

## Services

If you have several engines, pass `config_entry_id` to choose one; it is optional otherwise.

- `smart_frame_engine.set_ambient_context` (needs `ambient_context`): `family_mode`, `presence`,
  `weather`, `season`, `boost_tags`, `boost_people` and `ttl_minutes` (1 to 10080). Fields you
  leave out keep their value; an empty text or list clears that field.
- `smart_frame_engine.clear_ambient_context` (needs `ambient_context`): optional `field` to clear
  only one field.
- `smart_frame_engine.send_title_card` (needs `title_card`): `title`, `subtitle`, `caption`,
  `accent`, `ephemeral`, `ttl_minutes`, `hold_minutes`, `push_to_fraimic`.

```yaml
action: smart_frame_engine.set_ambient_context
data:
  weather: rainy
  presence: [Alice, Bob]
  ttl_minutes: 120
```

A service called on an engine that lacks the capability fails with a clear message instead of
sending a request.

## License

[MIT](LICENSE)
