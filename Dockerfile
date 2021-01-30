FROM python:3.9-alpine AS build

WORKDIR /app
ENV PIP_NO_CACHE_DIR="true"

COPY Pipfile* /app
COPY fritzbox_exporter.py /app

RUN pip --no-cache-dir install pipenv && \
    pipenv lock --keep-outdated --requirements > requirements.txt


FROM python:3.9-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787

WORKDIR /app
ENV PIPENV_VENV_IN_PROJECT="true"
COPY --from=build /app /app
RUN pip install -r requirements.txt

USER nobody
CMD ["python3", "-m", "fritzbox_exporter"]
