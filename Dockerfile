# Pull the generated requirements.txt and install into system using pip
FROM python:3.10-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true"
ENV PIPENV_VENV_IN_PROJECT=1

WORKDIR /app

COPY Pipfile* /app/
RUN pip --no-cache-dir install pipenv && \
    pipenv sync --system

COPY fritzexporter/ /app/fritzexporter

ENTRYPOINT ["python", "-m", "fritzexporter"]
