[![Discord](https://badgen.net/discord/online-members/zGVYf58)](https://discord.gg/zGVYf58)
![GitHub Release](https://img.shields.io/github/v/release/jackjpowell/uc-intg-psn)
![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/jackjpowell/uc-intg-psn/total)
<a href="#"><img src="https://img.shields.io/maintenance/yes/2026.svg"></a>
[![Buy Me A Coffee](https://img.shields.io/badge/Buy_Me_A_Coffee&nbsp;☕-FFDD00?logo=buy-me-a-coffee&logoColor=white&labelColor=grey)](https://buymeacoffee.com/jackpowell)

# PlayStation Network integration for Unfolded Circle Remotes

Using [uc-integration-api](https://github.com/aitatoi/integration-python-library)

The driver lets you view your PlayStation Network gameplay activity with the Unfolded Circle Remote Two/3.

## Media Player
Supported attributes:
 - View Only

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
