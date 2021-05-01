# build will install pipenv and generate a requirements.txt
# This will take care of reproducible builds based on Pipfile.lock
FROM python:3.9-alpine AS build

WORKDIR /app
ENV PIP_NO_CACHE_DIR="true"

COPY Pipfile* /app/

RUN pip --no-cache-dir install pipenv && \
    pipenv lock --keep-outdated --requirements > requirements.txt


# Pull the generated requirements.txt and install into system using pip
FROM python:3.9-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787

WORKDIR /app

COPY --from=build /app/requirements.txt /app/requirements.txt
RUN pip install -r requirements.txt

COPY fritzexporter/ /app/fritzexporter

USER nobody
ENTRYPOINT ["python3", "-m", "fritzexporter"]
