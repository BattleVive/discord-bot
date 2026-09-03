# syntax=docker/dockerfile:1

FROM scratch AS build-apk-dependencies

COPY apk-build-deps.txt /apk-build-deps.txt


FROM scratch AS runtime-apk-dependencies

COPY apk-runtime-deps.txt /apk-runtime-deps.txt


FROM scratch AS python-dependencies

COPY app/requirements.lock /requirements.lock


FROM python:3.14.7-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_COMPILE=1

RUN --mount=type=bind,from=build-apk-dependencies,source=/apk-build-deps.txt,target=/tmp/apk-build-deps.txt,ro \
    xargs apk add --no-cache < /tmp/apk-build-deps.txt

RUN --mount=type=bind,from=python-dependencies,source=/requirements.lock,target=/tmp/requirements.lock,ro \
    --mount=type=cache,target=/root/.cache/pip \
    python -m pip install \
        --require-hashes \
        --no-compile \
        --target /opt/python \
        -r /tmp/requirements.lock


FROM python:3.14.7-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc

RUN --mount=type=bind,from=runtime-apk-dependencies,source=/apk-runtime-deps.txt,target=/tmp/apk-runtime-deps.txt,ro \
    xargs apk add --no-cache < /tmp/apk-runtime-deps.txt \
    && rm -rf \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.14 \
        /usr/local/lib/python3.14/ensurepip \
        /usr/local/lib/python3.14/site-packages/pip \
        /usr/local/lib/python3.14/site-packages/pip-*.dist-info

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/opt/python

COPY --from=builder /opt/python /opt/python
COPY app/assets /app/assets
COPY app/init-db /app/init-db
COPY app/main.py /app/main.py
COPY app/battlevive_bot /app/battlevive_bot

RUN addgroup -g 10001 -S battlevive \
    && adduser -u 10001 -S -D -H -G battlevive battlevive \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data

WORKDIR /app
USER 10001:10001
HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD ["python3", "-m", "battlevive_bot.health"]
CMD ["python3", "main.py"]
