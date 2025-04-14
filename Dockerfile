# Build using "poetry" in a separate build container
# The resulting .whl file is then installed in the actual runner container
FROM python:3.13.3-alpine AS build
WORKDIR /app

RUN apk add build-base libffi-dev openssl-dev rust cargo && \
    pip install --upgrade pip && \
    pip install poetry==1.8.2

COPY README.md /app/
COPY pyproject.toml /app
COPY poetry.lock /app
COPY fritzexporter /app/fritzexporter

RUN poetry build

# Build the actual runner
FROM python:3.13.3-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true"

WORKDIR /app

COPY --from=build /app/dist/*.whl /

RUN pip install /fritz_exporter-*.whl && \
    mkdir /etc/fritz && \
    rm /fritz_exporter-*.whl

ENTRYPOINT ["python", "-m", "fritzexporter"]
