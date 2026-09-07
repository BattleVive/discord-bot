FROM python:3.14.7-alpine3.24@sha256:c6ead215bfd31f1e433d968853b7a769989117115b728874824e6c0a27cb96fc
WORKDIR /app
ENV BATTLEVIVE_ASSETS_DIR=/app/assets
RUN addgroup -g 10001 -S battlevive && adduser -u 10001 -S -D -H -G battlevive battlevive
COPY services/image-renderer/requirements.lock /tmp/requirements.lock
RUN apk add --no-cache jpeg zlib && python -m pip install --require-hashes --no-cache-dir -r /tmp/requirements.lock
COPY services/image-renderer/app /app
COPY assets /app/assets
USER 10001:10001
CMD ["python3", "-m", "battlevive_renderer.renderer_service"]
