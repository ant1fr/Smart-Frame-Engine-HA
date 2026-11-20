# Smart Frame Engine for Home Assistant

[![Release](https://img.shields.io/github/v/release/ant1fr/Smart-Frame-Engine-HA)](https://github.com/ant1fr/Smart-Frame-Engine-HA/releases/latest)
[![CI](https://github.com/ant1fr/Smart-Frame-Engine-HA/actions/workflows/ci.yml/badge.svg)](https://github.com/ant1fr/Smart-Frame-Engine-HA/actions/workflows/ci.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![Home Assistant 2026.1.0+](https://img.shields.io/badge/Home%20Assistant-2026.1.0%2B-blue.svg)](https://www.home-assistant.io/)
[![License: MIT](https://img.shields.io/github/license/ant1fr/Smart-Frame-Engine-HA)](LICENSE)

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[Smart Frame Engine](https://github.com/ant1fr/Smart-Frame-Engine), installable through
[HACS](https://hacs.xyz/) or manually.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ant1fr&repository=Smart-Frame-Engine-HA&category=integration)

> **Status:** version 1.0.0. Discovery, setup, authentication, compatibility checks, entities,
> services and blueprints are in place and covered by automated tests that replay the engine's API
> contract (v1.2). It has **not yet been run against a real engine**: if something behaves
> differently from what is described here, please
> [open an issue](https://github.com/ant1fr/Smart-Frame-Engine-HA/issues).

## Requirements

- Home Assistant 2026.1.0 or newer
- A Smart Frame Engine reachable from Home Assistant

## Installation

### HACS (custom repository)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/ant1fr/Smart-Frame-Engine-HA` with category **Integration**.
3. Install **Smart Frame Engine** and restart Home Assistant.
4. Your engine is usually discovered automatically (Settings > Devices & services). Otherwise add
   it yourself: [![Add the integration to your Home Assistant.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=smart_frame_engine)

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

- `smart_frame_engine.show_now_playing` (needs `now_playing`): shows the album cover of the music
  playing. Pass a `media_player` and the artist, album, title, position and cover are read from it;
  anything you set yourself wins. Other fields: `artist`, `album`, `title`, `artwork_url`,
  `duration_seconds`, `position_seconds`, `style` (`sleeve` or `vinyl`), `show_title`,
  `enrich_artwork`, `ephemeral`, `ttl_seconds`, `hold_minutes`, `push_to_fraimic`.
- `smart_frame_engine.show_media_poster` (needs `display_tmdb`): finds a movie or series on TMDB
  and shows its poster. Pass a `media_player` and the title, type (movie or series) and season are
  read from it. Other fields: `query`, `media_type`, `season`, `year`, `language`, `fallback_url`,
  `ephemeral`, `ttl_minutes` (1 to 1440), `hold_minutes`, `push_to_fraimic`.

```yaml
action: smart_frame_engine.show_now_playing
data:
  media_player: media_player.kitchen
```

A service called on an engine that lacks the capability fails with a clear message instead of
sending a request.

### Where the picture comes from

- **Album covers:** the cover of the media player is sent to the engine and **tried first**. If it
  is missing or unusable, the engine looks one up itself (Deezer, then MusicBrainz / Cover Art
  Archive). Set `use_media_player_picture: false` to always let the engine look it up.
- **Movie and series posters:** the engine looks the title up on TMDB, and the picture of the media
  player is only the **fallback**, used when TMDB has no match.
- The picture of a media player is a Home Assistant address, so the engine must be able to reach
  Home Assistant's **internal URL** (Settings > System > Network). Without an internal URL no
  picture is sent, and the engine's own lookup applies.

## Blueprints

Ready-to-use automations are in [`blueprints/automation/smart_frame_engine`](blueprints/automation/smart_frame_engine).
HACS does not install blueprints: import each one with its button (Settings > Automations >
Blueprints), or copy the files into `config/blueprints/automation/smart_frame_engine/`.

| Blueprint | What it does | Import |
| --- | --- | --- |
| `ambient_weather.yaml` | Tells the engine about the weather of a weather entity | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fant1fr%2FSmart-Frame-Engine-HA%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsmart_frame_engine%2Fambient_weather.yaml) |
| `ambient_presence.yaml` | Tells the engine which people are home | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fant1fr%2FSmart-Frame-Engine-HA%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsmart_frame_engine%2Fambient_presence.yaml) |
| `title_card.yaml` | Shows a text card when your trigger fires | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fant1fr%2FSmart-Frame-Engine-HA%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsmart_frame_engine%2Ftitle_card.yaml) |
| `now_playing_cover.yaml` | Shows the album cover of the music playing | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fant1fr%2FSmart-Frame-Engine-HA%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsmart_frame_engine%2Fnow_playing_cover.yaml) |
| `tv_poster.yaml` | Shows the poster of the movie or series playing | [![Import](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fant1fr%2FSmart-Frame-Engine-HA%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fsmart_frame_engine%2Ftv_poster.yaml) |

The weather and presence blueprints re-send their value every 30 minutes, and each value expires
on its own after its lifetime, so the engine forgets it if Home Assistant stops updating it. Every
blueprint has an optional engine input, only needed when you have several engines.

## License

[MIT](LICENSE)
