# Building

The recommended way to run this exporter is from a docker container. The included Dockerfile will build the exporter using an python:alpine container using python3. The Dockerfile uses a multi-stage build to first create the virtual environment from the Pipfile and the builds a clean python:3-alpine image with the exporter.

## Docker images

### docker-compose

There are two example docker-compose files in the docs folder. The file `docker-compose.local.yml` will build and run the exporter from the local directory.

You can simply run

```bash
cd docs
docker-compose -f docker-compose.local.yml up -d
```

to build and run the container from local sources. Configuration will be done via environment vars as specified in the docker-compose file.

### docker build

To build execute

```bash
docker build -t fritz_exporter:latest .
```

from inside the source directory.

To run the resulting image use

```bash
docker run -d --name fritz_exporter -p <PORT>:<FRITZ_EXPORTER_PORT> -e FRITZ_EXPORTER_CONFIG="192.168.178.1,username,password" fritz_exporter:latest
```

## Verify correct operation

```bash
curl localhost:<FRITZ_EXPORTER_PORT>
```

```text
# HELP python_gc_objects_collected_total Objects collected during gc
# TYPE python_gc_objects_collected_total counter
python_gc_objects_collected_total{generation="0"} 481.0
python_gc_objects_collected_total{generation="1"} 112.0
python_gc_objects_collected_total{generation="2"} 0.0
# HELP python_gc_objects_uncollectable_total Uncollectable object found during GC
# TYPE python_gc_objects_uncollectable_total counter
python_gc_objects_uncollectable_total{generation="0"} 0.0
python_gc_objects_uncollectable_total{generation="1"} 0.0
python_gc_objects_uncollectable_total{generation="2"} 0.0
...
```
