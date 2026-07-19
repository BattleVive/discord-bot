FROM python:3.14.6-alpine AS builder

COPY apk-build-deps.txt .
RUN apk add --no-cache $(cat apk-build-deps.txt | tr '\n' ' ')

RUN python -m venv /venv
COPY app/requirements.txt .
RUN /venv/bin/pip install --no-cache-dir -r requirements.txt


FROM python:3.14.6-alpine

COPY apk-runtime-deps.txt .
RUN apk add --no-cache $(cat apk-runtime-deps.txt | tr '\n' ' ')

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

COPY app /app
WORKDIR /app
CMD ["python3", "main.py"]
