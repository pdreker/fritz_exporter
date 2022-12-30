# Pull the generated requirements.txt and install into system using pip
FROM python:3.11.1-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true" \
    POETRY_NO_INTERACTION=1

WORKDIR /app

# Add compiler for AARCH64
RUN apk add --no-cache --virtual .fritzexporter_deps build-base python3-dev libffi-dev

RUN pip install poetry

COPY poetry.lock pyproject.toml fritzexporter/_version.py /app/
RUN  poetry config virtualenvs.create false \
     && poetry install --no-root --no-interaction --no-ansi

COPY fritzexporter/ /app/fritzexporter

ENTRYPOINT ["python", "-m", "fritzexporter"]
