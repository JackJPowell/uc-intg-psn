FROM python:3.11-slim-bullseye AS wheels

RUN apt-get update && apt-get install -y --no-install-recommends build-essential
WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt
RUN pip download --no-deps --no-binary=:all: --dest /sources playdirector==0.4.0

FROM python:3.11-slim-bullseye

WORKDIR /app

COPY ./requirements.txt requirements.txt
COPY --from=wheels /wheels /wheels
COPY --from=wheels /sources /sources
RUN pip3 install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt
RUN mkdir /config

ADD . .
RUN python tools/package_sources.py /app --library-source /sources/playdirector-0.4.0.tar.gz

ENV UC_DISABLE_MDNS_PUBLISH="false"
ENV UC_MDNS_LOCAL_HOSTNAME=""

ENV UC_INTEGRATION_INTERFACE="0.0.0.0"
ENV UC_INTEGRATION_HTTP_PORT="9090"

ENV UC_CONFIG_HOME="/config"
LABEL org.opencontainers.image.source https://github.com/jackjpowell/uc-intg-psn

CMD ["python3", "-u", "intg-psn/driver.py"]
