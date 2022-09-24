Running
=======

plain Docker (docker run)
-------------------------

Docker images are automatically pushed to Docker Hub and are built for linux/amd64, linux/arm/v6, linux/arm/v7 and linux/arm64, the latter three being useful for e.g. Raspberry Pi type systems.

To run simply use

.. code-block:: bash

  docker run -d -e FRITZ_USERNAME="prometheus" -e FRITZ_PASSWORD="monitoring" -p 9787:9787 --name fritz_exporter pdreker/fritz_exporter


This will use the default hostname of ``fritz.box`` for the device and use the default name of `Fritz!Box`

If you are monitoring multiple device you must use the config file method like this:

.. code-block:: bash

  docker run -d -v /path/to/fritz-exporter.yaml:/app/fritz-exporter.yaml -p 9787:9787 --name fritz_exporter pdreker/fritz_exporter --config /app/fritz-exporter.yaml

``/path/to/fritz-exporter.yaml`` is your local copy of the configuration, which will be mounted into the container.

See the example config file provided at :ref:`config-file`.

docker-compose
--------------

Config via environment variables
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

To run the exporter from docker-compose create an empty directory ``fritz-exporter``and put a file ``docker-compose.yml`` in the directory (replace the environment variables with appropriate values) with the following content:

.. code-block:: yaml

  # Example file for running tzhe exporter from the published image at hub.docker.com
  version: "3.8"
  services:
    fritz-exporter:
      image: pdreker/fritz_exporter:2
      container_name: fritz-exporter
      restart: always
      environment:
        FRITZ_HOSTNAME: 'fritz.box'
        FRITZ_USERNAME: 'prometheus'
        FRITZ_PASSWORD: 'prompassword'
      ports:
        - "9787:9787"

Then run ``docker compose up -d`` to start the exporter.

Config via config file
^^^^^^^^^^^^^^^^^^^^^^

Create an empty directory ``fritz-exporter`` and put a file ``docker-compose.yml`` in the directory (check the correct path for the config file) with the following content:

.. code-block:: yaml

  version: "3.8"
  services:
    fritz-exporter:
      image: pdreker/fritz_exporter:2
      command: --config /fritz-exporter.yml
      build: ../
      container_name: fritz-exporter
      restart: always
      ports:
        - "9787:9787"
      volumes:
        - "/path/to/fritz-exporter.yml:/fritz-exporter.yml"

Create a config file for the exporter in this directory named ``fritz-exporter.yml``. See the example config file provided at :ref:`config-file`.

Bare Metal (no container)
-------------------------

Running directly from sources is not recommended and should only be used for development. Please run this from docker/containers for real use.

This exporter requires Python >=3.10.

This project uses poetry (as of v2.1.2) to manage dependecies. As such you can simply recreate the neccessary virtual environment for this exporter by running ``poetry install`` from the checked out repository.

The exporter can directly be run from a shell. Set the environment vars or config file as described in the configuration section of this README and run ``python3 -m fritzbox_exporter [--config /path/to/config/file.yaml]`` from the code directory.
