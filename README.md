[![Discord](https://badgen.net/discord/online-members/zGVYf58)](https://discord.gg/zGVYf58)
![GitHub Release](https://img.shields.io/github/v/release/jackjpowell/uc-intg-psn)
![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/jackjpowell/uc-intg-psn/total)
<a href="#"><img src="https://img.shields.io/maintenance/yes/2026.svg"></a>
[![Buy Me A Coffee](https://img.shields.io/badge/Buy_Me_A_Coffee&nbsp;☕-FFDD00?logo=buy-me-a-coffee&logoColor=white&labelColor=grey)](https://buymeacoffee.com/jackpowell)

# PlayStation Network integration for Unfolded Circle Remotes

Using [uc-integration-api](https://github.com/aitatoi/integration-python-library)

The driver connects PlayStation Network and optional local PS4/PS5 Remote Play control to the Unfolded Circle Remote Two/3.

## Features

- A game browser with played history, installed PS5 titles, playtime and game details.
- Direct PS5 game launching through Sony's authenticated cloud command service.
- Local wake/rest, navigation and controller buttons through playdirector 0.4.
- Media player, Remote controller, power switch, a profile selector and ten sensors when PSN and a console are configured.
- PSN-only viewing or account-ID-only local controls; local commands remain available when PSN authentication expires.

Choose **PSN and console controls** during setup for the recommended path. Setup includes links to sign in to PSN and retrieve an NPSSO token, obtains your account ID automatically, then searches the local network for consoles. Select your console and enter the eight-digit Remote Play PIN from its settings. If multiple cloud consoles need to be distinguished, select one by name; no console DUID entry is needed. **PSN only** skips discovery and pairing. **Console controls without PSN** is an advanced path for users who already have their numeric/Base64 account ID. Updating credentials preserves existing IDs and pairing; additional profiles can be registered through device setup. Existing playdirector pairing records are converted after checking the console's LAN identity.

Installed titles and direct launching require PSN connectivity. Inventory includes games and apps and is unavailable when Sony omits it. The profile selector chooses saved console pairings, not arbitrary console users. Both collections are available directly in the media browser.

Local control uses [playdirector](https://github.com/JackJPowell/playdirector), installed from PyPI. See [third-party notices](THIRD_PARTY_NOTICES.md) for its GPL-3.0-only license and source.

## Usage
The simpliest way to get started is by uploading this integration to your unfolded circle remote. You'll find the option on the integration tab in the web configurator. Simply upload the .tar.gz file attached to the release. This option is nice and doesn't require a separate docker instance to host the package. However, upgrading is a fully manual process. To help with this, a docker image is also provided that allows you to run it externally from the remote and easily upgrade when new versions are released. 

### Docker
```
docker run -d --name=uc-intg-psn  --network host -v $(pwd)/<local_directory>:/config --restart unless-stopped ghcr.io/jackjpowell/uc-intg-psn:latest
```

### Docker Compose
```
services:
  uc-intg-psn:
    image: ghcr.io/jackjpowell/uc-intg-psn:latest
    container_name: uc-intg-psn
    network_mode: host
    volumes:
      - ./<local_directory>:/config
    environment:
      - UC_INTEGRATION_HTTP_PORT=9090
    restart: unless-stopped
```
