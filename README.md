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
