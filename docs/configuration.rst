Configuration
=============

The exporter supports two methods of configuration:

* via environment variable
* via config file

.. _environment-config:

Environment variable
--------------------

If you only need a single device this is the easiest way to configure the exporter.

+------------------------------+----------------------------------------------------+-----------+
| Env variable                 | Description                                        | Default   |
+==============================+====================================================+===========+
| ``FRITZ_NAME``               | User-friendly name for the device                  | Fritz!Box |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_HOSTNAME``           | Hostname of the device                             | fritz.box |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_USERNAME``           | Username to authenticate on the device             | none      |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_PASSWORD``           | Password to use for authentication                 | none      |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_PASSWORD_FILE``      | File to read the password from                     |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_LISTEN_ADDRESS``     | Address to listen on. Can be IPv4 or IPv6.         | 127.0.0.1 |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_PORT``               | Listening port for the exporter                    | 9787      |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_LOG_LEVEL``          | Application log level: ``DEBUG``, ``INFO``,        | INFO      |
|                              | ``WARNING``, ``ERROR``, ``CRITICAL``               |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_HOST_INFO``          | Enable extended information about all WiFi         | False     |
|                              | hosts. Only "true" or "1" will enable this feature |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_WIFI_CLIENT_INFO``   | Enable per-client WiFi metrics (signal/speed).     | False     |
|                              | Only "true" or "1" will enable this feature.       |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_CONNECTION_TIMEOUT`` | Optional per-device TR-064 connect timeout in      |           |
|                              | seconds. ``0`` or unset means no timeout.          |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_USE_TLS``            | Use HTTPS/TLS for TR-064 to the device.            | False     |
|                              | Only ``true`` or ``1`` enable this. Certificate    |           |
|                              | verification is disabled by ``fritzconnection``    |           |
|                              | (Fritz!Box self-signed certs).                     |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_DEVICE_PORT``        | Optional TR-064 port on the device. Defaults to    |           |
|                              | ``49000`` (HTTP) or ``49443`` (TLS) via            |           |
|                              | ``fritzconnection``. Distinct from ``FRITZ_PORT``  |           |
|                              | (exporter listen port). ``0`` or unset = default.  |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_REMOTE_ACCESS``      | Use AVM WAN remote TR-064 (``/tr064`` URL prefix). | False     |
|                              | Requires ``FRITZ_USE_TLS=true``. Only ``true`` or  |           |
|                              | ``1`` enable this.                                 |           |
+------------------------------+----------------------------------------------------+-----------+

.. note::

  enabling ``FRITZ_HOST_INFO`` by setting it to ``true`` or ``1`` will collect extended information about every device known your fritz device which can take a long time (20+ seconds). If you really want or need the extended stats please make sure that your Prometheus scraping interval and timeouts are set accordingly.

.. note::

  ``FRITZ_REMOTE_ACCESS`` / ``remote_access: true`` enables AVM's WAN remote TR-064 mode
  (path prefix ``/tr064``). It requires TLS (``use_tls: true`` / ``FRITZ_USE_TLS=true``)
  and a hostname/port reachable via Fernwartung (often a DynDNS/MyFRITZ name and
  forwarded HTTPS port). LAN scrapes should leave this disabled (default).

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
      wifi_client_info: True # optional, per-client WiFi signal/speed (higher cardinality)
      connection_timeout: 10 # optional, seconds; 0 disables timeout
      use_tls: false # optional; true = HTTPS TR-064 (default port 49443)
      port: 49000 # optional TR-064 port; omit for fritzconnection defaults
      remote_access: false # optional; true = WAN TR-064 (/tr064 prefix; requires use_tls)
    - name: Repeater Wohnzimmer # optional
      hostname: repeater-Wohnzimmer
      username: prometheus
      password_file: /path/to/password.txt

.. note::

  Enabling ``FRITZ_HOST_INFO`` by setting it to ``true`` or ``1`` will collect extended information about every device known to your Fritz device, which can take a long time (20+ seconds). If you really want or need the extended stats, please make sure that your Prometheus scraping interval and timeouts are set accordingly.

.. note::

  Enabling ``FRITZ_WIFI_CLIENT_INFO`` (``true`` or ``1``) exposes per-station WiFi metrics (signal strength and negotiated speed) for every associated client, on the box and on mesh repeaters alike. This adds one time series per connected client, so it is disabled by default — enable it only if you want per-client visibility and are aware of the extra cardinality.
