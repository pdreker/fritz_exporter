# Build .whl in a separate build container
FROM python:3.15.0b2-alpine AS build
WORKDIR /app

RUN apk add build-base libffi-dev openssl-dev rust cargo && \
    pip install --upgrade pip build

COPY README.md pyproject.toml /app/
COPY fritzexporter /app/fritzexporter

RUN python -m build --wheel

# Build the actual runner
FROM python:3.15.0b2-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true"

WORKDIR /app

COPY --from=build /app/dist/*.whl /

RUN pip install /fritz_exporter-*.whl && \
    mkdir /etc/fritz && \
    rm /fritz_exporter-*.whl

ENTRYPOINT ["python", "-m", "fritzexporter"]
