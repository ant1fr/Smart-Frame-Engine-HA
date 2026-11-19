# Smart Frame Engine for Home Assistant

Custom [Home Assistant](https://www.home-assistant.io/) integration for
[Smart Frame Engine](https://github.com/ant1fr/Smart-Frame-Engine), installable through
[HACS](https://hacs.xyz/) or manually.

> **Status:** work in progress. The foundation (discovery, setup, authentication, compatibility
> checks) is being built first; entities and services follow in later milestones.

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

## License

[MIT](LICENSE)
