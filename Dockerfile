# Pull the generated requirements.txt and install into system using pip
FROM python:3.11.3-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true"

WORKDIR /app

COPY dist/*.whl /
RUN pip install /fritz_exporter-*.whl && \
    mkdir /etc/fritz

ENTRYPOINT ["python", "-m", "fritzexporter"]
