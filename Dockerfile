FROM python:3.14-slim-bookworm AS dependencies
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /uvx /usr/local/bin/

# netifaces is distributed as source; keep its compiler out of the runtime image.
RUN apt-get update -o APT::Update::Error-Mode=any \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY . .
RUN uv sync --no-dev --no-install-project \
    && python tools/package_sources.py /app

FROM python:3.14-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /uvx /usr/local/bin/

ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --from=dependencies /opt/venv /opt/venv
COPY --from=dependencies /app /app
RUN mkdir /config

ENV UC_DISABLE_MDNS_PUBLISH="false" \
    UC_MDNS_LOCAL_HOSTNAME="" \
    UC_INTEGRATION_INTERFACE="0.0.0.0" \
    UC_INTEGRATION_HTTP_PORT="9090" \
    UC_CONFIG_HOME="/config"
LABEL org.opencontainers.image.source="https://github.com/jackjpowell/uc-intg-psn"

CMD ["python3", "-u", "intg-psn/driver.py"]
