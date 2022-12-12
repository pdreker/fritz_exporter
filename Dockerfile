# Pull the generated requirements.txt and install into system using pip
FROM python:3.11.1-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787
ENV PIP_NO_CACHE_DIR="true"

WORKDIR /app

COPY requirements.txt /app/
RUN pip install -r requirements.txt && \
    mkdir /etc/fritz

COPY fritzexporter/ /app/fritzexporter

ENTRYPOINT ["python", "-m", "fritzexporter"]
