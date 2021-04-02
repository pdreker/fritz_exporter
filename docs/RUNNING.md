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

When using the environment vars you can only specify a single device. If you need multiple devices please use the config file.

Example for a device (at 192.168.178.1 username monitoring and the password "mysupersecretpassword"):

```bash
export FRITZ_NAME='My Fritz!Box'
export FRITZ_HOSTNAME='192.168.178.1'
export FRITZ_USERNAME='monitoring'
export FRITZ_PASSWORD='mysipersecretpassword'
```

### Config file

The default location for the config file is `/app/fritz-exporter.yaml` (`/app` is the working directory of the exporter, so `./fritz-exporter.yaml` will pick up the file.)

## plain Docker (docker run)

Release images are automatically pushed to Docker Hub and are built for linux/amd64, linux/arm/v6, linux/arm/v7 and linux/arm64, the latter three being useful for e.g. Raspberry Pi type systems.

To run simply use

```bash
docker run -d -e FRITZ_USERNAME="prometheus" -e FRITZ_PASSWORD="monitoring" -p 9787:9787 --name fritz_exporter pdreker/fritz_exporter
```

This will use the default hostname of `fritz.box`for the device and use the default name of `Fritz!Box`

If you are monitoring multiple device you must use the config file method like this:

```bash
docker run -d -v ./fritz-exporter.yaml:/app/fritz-exporter.yaml -p 9787:9787 --name fritz_exporter pdreker/fritz_exporter
```

See the example config file provided at docs/fritz-exporter.yml

## docker-compose

While `docker-compose.hub.yml' will run the published image from Docker Hub. If you are not making changes to the code you will probably want to run from the published image.

## Kubernetes deployment

Put the Fritz!Box password into a kubernetes secret

```bash
kubectl create secret generic fritzbox-password --from-literal=password='mysupersecretpassword'
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
        app: fritzbox-exporter
    spec:
      containers:
      - name: fritzbox-exporter
        image: pdreker/fritz_exporter:latest
        imagePullPolicy: Always
        env:
        - name:  FRITZ_HOSTNAME
          value: "192.168.178.1"
        - name:  FRITZ_USERNAME
          value: "monitoring"
        - name:  FRITZ_EXPORTER_PORT
          value: "9787"
        - name:  FRITZ_PASSWORD
          valueFrom:
            secretKeyRef:
              name: fritzbox-password
              key:  password
        ports:
        - containerPort: 9787
```

## Bare Metal (no container)

* Python >=3.6
* fritzconnection >= 1.0.0
* prometheus-client >= 0.6.0

A Pipfile for `pipenv` ist included with the source. So after installing `pipenv` you can just type `pipenv install` inside the source directory and a virtual environment for running the exporter will be configured for you.

The exporter can directly be run from a shell. Set the environment vars or config file as described in the configuration section of this README and run `python3 -m fritzbox_exporter [--config /oath/to/config/file.yaml]` from the code directory.

### System Service

There is a sample systemd unit file included in the docs directory at docs/fritzbox_exporter.service. Please note that this is only a rough example and the author recommends running from some kind of Docker setup.
