# The requirements and the wheel are the same on every platform: build them once,
# on the build host, with the versions and hashes from uv.lock.
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 AS uv

FROM --platform=$BUILDPLATFORM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72 AS build
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

COPY README.md pyproject.toml uv.lock /app/
COPY fritzexporter /app/fritzexporter

RUN uv export --frozen --no-dev --no-emit-project --format requirements.txt \
        --output-file requirements.txt && \
    uv build --wheel

# Build the actual runner
FROM python:3.14.8-alpine@sha256:f6a589d43c42b9e7f7dc67a12d37132491f362859a5d750607710cc56da3bc72

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true" \
    PIP_DISABLE_PIP_VERSION_CHECK="true" \
    # Inside a container the exporter listens on the container's own address;
    # the published port decides who can reach it. Applies to the env
    # configuration; a config file sets listen_address itself.
    FRITZ_LISTEN_ADDRESS="0.0.0.0"

WORKDIR /app

COPY --from=build /app/requirements.txt /app/dist/*.whl /tmp/install/

RUN pip install --require-hashes --requirement /tmp/install/requirements.txt && \
    pip install --no-deps /tmp/install/*.whl && \
    rm -r /tmp/install && \
    mkdir /etc/fritz

# nobody, as in the Helm chart
USER 65534:65534

ENTRYPOINT ["python", "-m", "fritzexporter"]
