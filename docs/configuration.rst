Configuration
=============

The exporter supports two methods of configuration:

* via environment variable
* via config file

.. _environment-config:

Environment variable
--------------------

If you only need a single device this is the easiest way to configure the exporter.

+---------------------+----------------------------------------------------+-----------+
| Env variable        | Description                                        | Default   |
+=====================+====================================================+===========+
| ``FRITZ_NAME``      | User-friendly name for the device                  | Fritz!Box |
+---------------------+----------------------------------------------------+-----------+
| ``FRITZ_HOSTNAME``  | Hostname of the device                             | fritz.box |
+---------------------+----------------------------------------------------+-----------+
| ``FRITZ_USERNAME``  | Username to authenticate on the device             | none      |
+---------------------+----------------------------------------------------+-----------+
| ``FRITZ_PASSWORD``  | Password to use for authentication                 | none      |
+---------------------+----------------------------------------------------+-----------+
| ``FRITZ_PORT``      | Listening port for the exporter                    |      9787 |
+---------------------+----------------------------------------------------+-----------+
| ``FRITZ_LOG_LEVEL`` | Application log level: ``DEBUG``, ``INFO``,        | INFO      |
|                     | ``WARNING``, ``ERROR``, ``CRITICAL``               |           |
+---------------------+----------------------------------------------------+-----------+
| ``FRITZ_HOST_INFO`` | Enable extended information about all WiFi         | False     |
|                     | hosts. Only "true" or "1" will enable this feature |           |
+---------------------+----------------------------------------------------+-----------+

.. warning::

  enabling ``FRITZ_HOST_INFO`` by setting it to ``true`` or ``1`` will collect extended information about every device known your fritz device, which can take a long time. If you really want or need the extended stats please make sure, that your scraping interval and timeouts are set accordingly.

When using the environment vars you can only specify a single device. If you need multiple devices please use the config file.

Example for a device (at 192.168.178.1 username "monitoring" and the password "mysupersecretpassword"):

.. code-block:: bash

  export FRITZ_NAME='My Fritz!Box'
  export FRITZ_HOSTNAME='192.168.178.1'
  export FRITZ_USERNAME='monitoring'
  export FRITZ_PASSWORD='mysupersecretpassword'

.. _config-file:

Config file
-----------

To use the config file you have to specify the the location of the config and mount the appropriate file into the container. The location can be specified by using the ``--config`` parameter.

.. code-block:: yaml

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

.. warning::

  enabling ``FRITZ_HOST_INFO`` by setting it to ``true`` or ``1`` will collect extended information about every device known your fritz device, which can take a long time. If you really want or need the extended stats pleade make sure, that your scraping interval and timeouts are set accordingly.
