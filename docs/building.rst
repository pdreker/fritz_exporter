Building
========

The recommended way to run this exporter is from a docker container. The included Dockerfile will build the exporter using an python:alpine container using python3. The Dockerfile relies on a locally generated ``requirements.txt`` file and the builds a clean python:3-alpine image with the exporter.

Building and running from a local image
---------------------------------------

To build clone the repository from GitHub, enter the repository and execute

.. code-block:: bash

  poetry export -f requirements.txt --output requirements.txt
  docker build -t fritz_exporter:local .


from inside the source directory.

To run the resulting image use

.. code-block:: bash

  docker run -d --name fritz_exporter -p <PORT>:<FRITZ_EXPORTER_PORT> -e FRITZ_EXPORTER_CONFIG="192.168.178.1,username,password" fritz_exporter:local


Verify correct operation
^^^^^^^^^^^^^^^^^^^^^^^^

To verify correct operation just use curl against the running exporter. It should reply with a Prometheus-style list of metrics:

.. code-block:: bash

  > curl localhost:<FRITZ_EXPORTER_PORT>

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

.. note::

  If you have the ``host_info`` config enabled for one or more devices please be aware, that it may take a long time to receive the reply, as the metrics are read synchronuously when queried. Practical experience has shown that a device knowing around 70 WiFi devices may take 20-30s to reply to all metrics queries. So set appropriate timeouts.

Building and running locally (no containers)
--------------------------------------------

For development and debugging it may be neccessary or simpler to run the exporter directly without docker. To do this simply install the dependencies into a virtual environment using ``poetry install``. You can then enter the venv using ``poetry shell``.

To run the exporter just use ``python -m fritzexporter --config /path/to/config.yaml`` or set environment variables as described in :ref:`environment-config`.
