# syntax=docker/dockerfile:1

FROM scratch AS build-apk-dependencies

COPY apk-build-deps.txt /apk-build-deps.txt


FROM scratch AS runtime-apk-dependencies

COPY apk-runtime-deps.txt /apk-runtime-deps.txt


FROM scratch AS python-dependencies

COPY app/requirements.lock /requirements.lock


FROM python:3.14.6-alpine3.24 AS builder

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


FROM python:3.14.6-alpine3.24

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
COPY app/main.py /app/main.py
COPY app/battlevive_bot /app/battlevive_bot

WORKDIR /app
CMD ["python3", "main.py"]
