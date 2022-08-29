# Running the exporter

It is recommended to run the exporter from a Docker container. Build system for Docker is included in this repository (see docs/BUILD.md) or you can use the offical Docker image from <https://hub.docker.com/pdreker/fritz_exporter>

## Configuration

The exporter supports two methods of configuration:

* via environment variable
* via config file

### Environment variable

If you only need a single device this is the easiest way to configure the exporter.

| Env variable | Description | Default |
|--------------|-------------|---------|
| FRITZ_NAME   | User-friendly name for the device | Fritz!Box |
| FRITZ_HOSTNAME | Hostname of the device | fritz.box |
| FRITZ_USERNAME | Username to authenticate on the device | none |
| FRITZ_PASSWORD | Password to use for authentication | none |
| FRITZ_PORT   | Listening port for the exporter | 9787 |
| FRITZ_LOG_LEVEL | Application log level: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` | INFO |
| FRITZ_HOST_INFO | Enable extended information about all WiFi hosts. Only "true" or "1" will enable this feature | False |

**WARNING** setting `FRITZ_HOST_INFO` to `true` will collect extended information about every device known your fritz device, which can take a long time. If you really want or need the extended stats please make sure, that your scraping interval and timeouts are set accordingly.

When using the environment vars you can only specify a single device. If you need multiple devices please use the config file.

Example for a device (at 192.168.178.1 username monitoring and the password "mysupersecretpassword"):

```bash
export FRITZ_NAME='My Fritz!Box'
export FRITZ_HOSTNAME='192.168.178.1'
export FRITZ_USERNAME='monitoring'
export FRITZ_PASSWORD='mysupersecretpassword'
```

### Config file

To use the config file you have to specify the the location of the config and mount the appropriate file into the container. The location can be specified by using the `--config` parameter.

```yaml
# Full example config file for Fritz-Exporter
exporter_port: 9787 # optional
log_level: DEBUG # optional
devices:
  - name: Fritz!Box 7590 Router # optional
    hostname: fritz.box
    username: prometheus
    password: prometheus
    host_info: True
  - name: Repeater Wohnzimmer # optional
    hostname: repeater-Wohnzimmer
    username: prometheus
    password: prometheus
```

**WARNING** setting `host_info` to `true` will collect extended information about every device known your fritz device, which can take a long time. If you really want or need the extended stats pleade make sure, that your scraping interval and timeouts are set accordingly.

## plain Docker (docker run)

Release images are automatically pushed to Docker Hub and are built for linux/amd64, linux/arm/v6, linux/arm/v7 and linux/arm64, the latter three being useful for e.g. Raspberry Pi type systems.

To run simply use

```bash
docker run -d -e FRITZ_USERNAME="prometheus" -e FRITZ_PASSWORD="monitoring" -p 9787:9787 --name fritz_exporter pdreker/fritz_exporter
```

This will use the default hostname of `fritz.box`for the device and use the default name of `Fritz!Box`

If you are monitoring multiple device you must use the config file method like this:

```bash
docker run -d -v /path/to/fritz-exporter.yaml:/app/fritz-exporter.yaml -p 9787:9787 --name fritz_exporter pdreker/fritz_exporter --config /app/fritz-exporter.yaml
```

See the example config file provided at docs/fritz-exporter.yml

## docker-compose

There are some docker-compose sample files in the `docs/` directory:

* `docker-compose.hub.yml` - Runs with environment config from the official docker hub image
* `docker-compose.local.yml` - Runs with environment config from a locally built image
* `docker-compose.local-file.yml` - Runs with config file from local build

## Kubernetes deployment

Put the configuration file into a kubernetes secret like this:

```bash
kubectl create secret generic fritz-exporter-config --from-file=fritz-exporter.yaml
```

then deploy the exporter with a deployment

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: fritzbox-exporter
  labels:
    app: fritzbox-exporter
spec:
  selector:
    matchLabels:
      app: fritzbox-exporter
  template:
    metadata:
      annotations:
        prometheus.io/scrape: "true"
        prometheus.io/path: /
        prometheus.io/port: "9787"
      labels:
        app: fritz-exporter
    spec:
      containers:
      - name: fritz-exporter
        args:
        - '--config'
        - '/etc/fritz-exporter/fritz-exporter.yaml'
        image: pdreker/fritz_exporter:latest
        imagePullPolicy: Always
        volumeMounts:
        - name: config
          mountPath: /etc/fritz-exporter
          readOnly: true
      volumes:
      - name: config
        secret:
          secretName: fritz-exporter-config
      ports:
        - containerPort: 9787
```

## Bare Metal (no container)

* Python >=3.6
* fritzconnection >= 1.0.0
* prometheus-client >= 0.6.0
* PyYAML

A Pipfile for `pipenv` ist included with the source. So after installing `pipenv` you can just type `pipenv install` inside the source directory and a virtual environment for running the exporter will be configured for you.

The exporter can directly be run from a shell. Set the environment vars or config file as described in the configuration section of this README and run `python3 -m fritzbox_exporter [--config /oath/to/config/file.yaml]` from the code directory.

### System Service

There is a sample systemd unit file included in the docs directory at docs/fritzbox_exporter.service. Please note that this is only a rough example and the author recommends running from some kind of Docker setup.
