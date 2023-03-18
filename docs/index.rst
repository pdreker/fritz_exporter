.. fritz-exporter documentation master file, created by
   sphinx-quickstart on Sat Sep 24 13:59:17 2022.
   You can adapt this file completely to your liking, but it should at least
   contain the root `toctree` directive.

Welcome to fritz-exporter's documentation!
==========================================

.. toctree::
   :maxdepth: 2
   :caption: Contents:

   quickstart
   upgrading
   configuration
   running
   docker-images
   helping_out
   building
   coding

Fritz! exporter for prometheus
==============================

This is a prometheus exporter for AVM Fritz! home network devices commonly found in Europe. This exporter uses the devices builtin TR-064 API via the fritzconnection python module.

The exporter should work with Fritz!Box and Fritz!Repeater Devices (and maybe others). It actively checks for supported metrics and queries the metrics for all devices configured (Yes, it has multi-device support for all you Mesh users out there.)

It has been tested against an AVM Fritz!Box 7590 (DSL), a Fritz!Repeater 2400 and a Fritz!WLAN Repeater 1750E. If you have another box and data is missing, please file an issue or PR on GitHub.


.. note::
  **Prometheus required**

  As the scope of this exporter lies on a typical home device, this also means that there are a lot of people interested in it, who may not have had any contact with `Prometheus <https://prometheus.io/>`_. As a result of this there have been some misunderstandings in the past, how this all works.

  To avoid frustration you will need to know this:

  **You must setup and configure Prometheus separately!** If you are running in plain docker or docker-compose there is a docker-compose setup for Prometheus at https://github.com/vegasbrianc/prometheus which also includes Grafana to actually produce dashboards. This may work out of the box or can be used as a starting point.

  The whole setup required is:

  * fritz_exporter: connects to your Fritz device, reads the metrics and makes them available in a format Prometheus understands
  * prometheus: connects to the exporter at regular time intervals, reads the data and stores it in its database
  * grafana: connects to prometheus and can query the database of metrics for timeseries and create dashboards from it.

  Check out the :ref:`quickstart`, which will bring up a simple and limited Prometheus, Grafana and exporter setup.

  **You cannot connect grafana to the exporter directly. This will not work**.

Metrics
-------

The following groups of metrics are currently available:

* Base Information (Model, Serial, Software Version, Uptime)
* Software Information (Update available)
* LAN statistics (Ethernet only)
* WAN statistics
* DSL statistics
* PPP statistics
* WiFi statistics
* WAN Layer1 (physical link) statistics

If there is any information missing or not displayed on your specific device, please open an issue on GitHub.

Known Problems
--------------

* It seems like Fritz!OS does not internally count the packets for the Guest WiFi. So even though those counters are there they are always 0. This seems to be a problem with Fritz!OS and not the exporter. The counters are delivered nontheless, just in case this gets fixed by AVM.
* If you receive ``Fatal Python error: init_interp_main: can't initialize time`` when running the container you may have to update libseccomp on your Docker host. This issue mainly happens on Raspberry Pi and is triggered by a version of libseccomp2 which is too old. See https://askubuntu.com/questions/1263284/apt-update-throws-signature-error-in-ubuntu-20-04-container-on-arm (Method 2) and https://github.com/pdreker/fritz_exporter/issues/38.
* On some boxes LAN Packet counters are stuck at 0 even though the box reports the stats as available.
* Fritz!OS does not allow passwords longer than 32 characters (as of 07.25). If you try to use a longer password, the admin ui will simply discard all characters after the 32nd. The UI will also cut your inserted password down to 32 characters. So you will be able to login in the UI with the long password. The exporter however does not alter your password and requests will result in a ``401 Unauthorized`` error. So please be aware of this limit and choose a suitable password.
* Collecting HostInfo (disabled by default) can be extremely slow and will cause some load on the device. It works, but it is slow as this feature needs two calls to the Fritz! device for every device it knows which will simply take some time. If you enable this, make sure your Prometheusm `scrape_timeouts` are set appropriately (30s should be OK for most setups, but you may need to go even higher).

Grafana Dashboards
------------------

There is a Grafana dashboard available at https://grafana.com/grafana/dashboards/13983-fritz-exporter/.
If the host info metrics are enabled a dashboard also using those metrics is available at https://grafana.com/grafana/dashboards/17751-fritz-exporter-dash/.

Helm Chart
----------

There is a (rather crude) Helm chart under ``helm`` in the `repository <https://github.com/pdreker/fritz_exporter>`_. It will deploy the exporter and also create a service monitor for Prometheus Operator to automatically scrape the exporter.

Helping out
-----------

If your device delivers some metrics which are not yet scraped by this exporter you can either create a Pull Request, which will be gladly accepted ;-)

Alternatively you can use the following commands and the litlle helper script in the root of this repository to let me know of metrics:

.. code-block:: bash

  fritzconnection -i <FRITZ-IP> -s # Lists available services
  fritzconnection -i <FRITZ-IP> -S <ServiceName> # Lists available action for a service
  python -m fritz_export_helper <FRITZ-IP> <USERNAME> <PASSWORD> <ServiceName> <ActionName> # Will output the data returned from the device in a readable format

If you have found something you need/want, open an issue provide the following infos:

1. Model of Device (e.g. FritzBox DSL 7590)
2. ServiceName and ActionName
3. Output from fritz_export_helper (make sure, there is not secret data, like password or WiFi passwords in there, before sending!)
4. What output you need/want and a little bit of info of the environment (Cable, DSL, Hybrid LTE Mode, ... whatever might be relevant)


Disclaimer
----------

Fritz! and AVM are registered trademarks of AVM GmbH. This project is not associated with AVM or Fritz other than using the devices and their names to refer to them.

Copyright
---------

Copyright 2019-2023 Patrick Dreker <patrick@dreker.de>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  <http://www.apache.org/licenses/LICENSE-2.0>

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
