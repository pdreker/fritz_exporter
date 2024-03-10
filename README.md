# Fritz! exporter for prometheus

[![Reliability Rating](https://sonarcloud.io/api/project_badges/measure?project=pdreker_fritz_exporter&metric=reliability_rating)](https://sonarcloud.io/summary/new_code?id=pdreker_fritz_exporter) [![Maintainability Rating](https://sonarcloud.io/api/project_badges/measure?project=pdreker_fritz_exporter&metric=sqale_rating)](https://sonarcloud.io/summary/new_code?id=pdreker_fritz_exporter) [![Security Rating](https://sonarcloud.io/api/project_badges/measure?project=pdreker_fritz_exporter&metric=security_rating)](https://sonarcloud.io/summary/new_code?id=pdreker_fritz_exporter) [![Coverage](https://sonarcloud.io/api/project_badges/measure?project=pdreker_fritz_exporter&metric=coverage)](https://sonarcloud.io/summary/new_code?id=pdreker_fritz_exporter) [![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=pdreker_fritz_exporter&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=pdreker_fritz_exporter)

![ReadTheDocs](https://readthedocs.org/projects/docs/badge/?version=latest) ![Dependabot](https://img.shields.io/badge/dependabot-025E8C?style=flat&logo=dependabot&logoColor=white) ![Tests](https://img.shields.io/github/actions/workflow/status/pdreker/fritz_exporter/run-tests.yaml?label=Tests) ![Build](https://img.shields.io/github/actions/workflow/status/pdreker/fritz_exporter/build-trunk.yaml?branch=main)

This is a prometheus exporter for AVM Fritz! home network devices commonly found in Europe. This exporter uses the devices builtin TR-064 API via the fritzconnection python module.

The exporter should work with Fritz!Box and Fritz!Repeater Devices (and maybe others). It actively checks for supported metrics and queries the for all devices configured (Yes, it has multi-device support for all you Mesh users out there.)

It has been tested against an AVM Fritz!Box 7590 (DSL), a Fritz!Repeater 2400 and a Fritz!WLAN Repeater 1750E. If you have another box and data is missing, please file an issue or PR on GitHub.

## Documentation

Check out the full documentation at [ReadTheDocs](https://fritz-exporter.readthedocs.io/)

## Attention - Prometheus required

As the scope of this exporter lies on a typical home device, this also means that there are a lot of people interested in it, who may not have had any contact with [Prometheus](https://prometheus.io/). As a result if this there have been some misunderstandings in the past, how this all works.

To avoid frustration you will need to know this:

**You must setup and configure Prometheus separately!** If you are running in plain docker or docker-compose there is a docker-compose setup for Prometheus at <https://github.com/vegasbrianc/prometheus> which also includes Grafana to actually produce dashboards. This may work out of the box or can be used as a starting point.

The whole setup required is:

* fritz_exporter: connects to your Fritz device, reads the metrics and makes them available in a format Prometheus understands
* prometheus: connects to the exporter at regular time intervals, reads the data and stores it in its database
* grafana: connects to prometheus and can query the database of metrics for timeseries and create dashboards from it.

**You cannot connect grafana to the exporter directly. This will not work**.

Please check the "Quickstart" in the documentation at [ReadTheDocs](https://fritz-exporter.readthedocs.io) for a simple setup.

## Disclaimer

Fritz! and AVM are registered trademarks of AVM GmbH. This project is not associated with AVM or Fritz other than using the devices and their names to refer to them.

## Copyright

Copyright 2019-2024 Patrick Dreker <patrick@dreker.de>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  <http://www.apache.org/licenses/LICENSE-2.0>

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
