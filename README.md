# Fritz!Box exporter for prometheus

Simple and straight forward exporter for AVM Fritz!Box metrics. This exporter uses the Fritz!Box API (via python module "fritzconnection") as opposed to using UPnP. As of now this has tons of assumptions behind it, so it may or may not work against your Fritz!Box. It has been tested against an AVM Fritz!Box 7590 (DSL). If you have another box and data is missing, please file an issue or PR on GitHub.

## Building and running

### Requirements

* Python >=3.6
* fritzconnection >= 0.6.5
* prometheus-client >= 0.6.0

A requirements.txt file is included with the source. Install the requirements using `pip install -r requirements.txt`, preferably inside a virtual environment so your local python packages are not messed up.

### System Service
This exporter can directly be run from a shell. Set the environment vars as describe in the configuration section of this README and run "python3 -m fritzbox_exporter" from the code directory.
There is a sample systemd unit file included in the docs directory.

### Docker
The recommended way to run this exporter is from a docker container. The included Dockerfile will build the exporter using an python:alpine container using python3.

To build execute

```bash
docker build -t fritzbox_exporter:latest .
```

from inside the source directory.

To run the resulting image use

```bash
docker run -d --name fritzbox_exporter -p <PORT>:<FRITZ_EXPORTER_PORT> -e FRITZ_USER=<YOUR_FRITZ_USER> -e FRITZ_PASS=<YOUR_FRITZ_PASS> fritzbox_exporter:latest
```

Verify correct operation:

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

## Configuration

Configuration is done completely via environment vars.

| Env variable | Description | Default |
|--------------|-------------|---------|
| FRITZ_HOST   | Hostname or IP where the Fritz!Box can be reached. | fritz.box |
| FRITZ_USER   | Username for authentication | none |
| FRITZ_PASS   | Password for authentication | none |
| FRITZ_EXPORTER_PORT | Listening port for the exporter | 9787 |

## Copyright

Copyright 2019-2021 Patrick Dreker <patrick@dreker.de>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
