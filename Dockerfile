FROM python:3-alpine

LABEL Name=fritzbox_exporter
EXPOSE 9787

WORKDIR /app
ADD . /app

# Using pip:
RUN apk add --no-cache libxml2 libxslt && \
    apk add --no-cache --virtual .build-deps gcc musl-dev libxml2-dev libxslt-dev && \
    python3 -m pip --no-cache-dir install -r requirements.txt && \
    apk del .build-deps

USER nobody
CMD ["python3", "-m", "fritzbox_exporter"]
