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
| ``FRITZ_EVENT_LOG``          | Write new router event log entries to the          | False     |
|                              | exporter's log. Only "true" or "1" will enable     |           |
|                              | this feature, see :ref:`event-log`.                |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_CONNECTION_TIMEOUT`` | Per-device connect/read timeout in seconds for     | 10        |
|                              | TR-064 and the web interface. ``0`` disables the   |           |
|                              | timeout.                                           |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_USE_TLS``            | Use HTTPS for TR-064 and the web interface.        | False     |
|                              | Only ``true`` or ``1`` enable this. The exporter   |           |
|                              | does not verify the certificate (Fritz!Box certs   |           |
|                              | are self-signed).                                  |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_DEVICE_PORT``        | Optional device port, see :ref:`ports`. Distinct   |           |
|                              | from ``FRITZ_PORT`` (exporter listen port).        |           |
|                              | ``0`` or unset = default.                          |           |
+------------------------------+----------------------------------------------------+-----------+
| ``FRITZ_REMOTE_ACCESS``      | Scrape over AVM WAN remote access, see             | False     |
|                              | :ref:`ports`. Requires ``FRITZ_USE_TLS=true``.     |           |
|                              | Only ``true`` or ``1`` enable this.                |           |
+------------------------------+----------------------------------------------------+-----------+

.. note::

  enabling ``FRITZ_HOST_INFO`` by setting it to ``true`` or ``1`` will collect extended information about every device known your fritz device which can take a long time (20+ seconds). If you really want or need the extended stats please make sure that your Prometheus scraping interval and timeouts are set accordingly.

.. _ports:

Ports, TLS and remote access
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The exporter talks to two interfaces on the device: TR-064 for most metrics, and
the web interface for smart home (AHA), DOCSIS and REST API metrics. Which ports
it uses depends on ``use_tls``, ``port`` and ``remote_access``:

+----------------------------------+---------------------------+---------------------------+
| Mode                             | TR-064                    | Web interface             |
+==================================+===========================+===========================+
| Local (default)                  | ``http``, port ``49000``  | ``http``, port ``80``     |
+----------------------------------+---------------------------+---------------------------+
| Local, ``use_tls: true``         | ``https``, port ``49443`` | ``https``, port ``443``   |
+----------------------------------+---------------------------+---------------------------+
| ``remote_access: true``          | ``https``, ``port``       | ``https``, ``port``       |
| (requires ``use_tls: true``)     | (default ``443``), path   | (default ``443``)         |
|                                  | prefix ``/tr064``         |                           |
+----------------------------------+---------------------------+---------------------------+

In local mode ``port`` sets the TR-064 port only; the web interface stays on 80 or 443.

In remote mode the device serves TR-064 and the web interface on one port: the
HTTPS port set for internet access to the device ("Internet > Permit Access >
FRITZ!Box Services"). Set ``hostname`` to the device's public name (often a MyFRITZ or
DynDNS name) and ``port`` to that port. LAN scrapes should leave remote access
disabled (default).

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
      event_log: false # optional, write new router event log entries to the exporter's log
      connection_timeout: 10 # optional, seconds; 0 disables timeout (default 10)
      use_tls: false # optional; true = HTTPS for TR-064 and the web interface
      port: 49000 # optional; TR-064 port locally, shared remote port with remote_access
      remote_access: false # optional; true = WAN remote access (requires use_tls)
    - name: Repeater Wohnzimmer # optional
      hostname: repeater-Wohnzimmer
      username: prometheus
      password_file: /path/to/password.txt

.. note::

  Enabling ``FRITZ_HOST_INFO`` by setting it to ``true`` or ``1`` will collect extended information about every device known to your Fritz device, which can take a long time (20+ seconds). If you really want or need the extended stats, please make sure that your Prometheus scraping interval and timeouts are set accordingly.

.. note::

  Enabling ``FRITZ_WIFI_CLIENT_INFO`` (``true`` or ``1``) exposes per-station WiFi metrics (signal strength and negotiated speed) for every associated client, on the box and on mesh repeaters alike. This adds one time series per connected client, so it is disabled by default — enable it only if you want per-client visibility and are aware of the extra cardinality.

.. _event-log:

Event log
---------

The Fritz!Box keeps its event log (*System > Event log* in the web interface) in RAM and
loses it on every restart, including the ones caused by a firmware update. With
``FRITZ_EVENT_LOG`` (``event_log`` in the config file) the exporter reads the complete log
over TR-064 (``DeviceInfo:X_AVM-DE_GetDeviceLogPath``) on every collection and writes each
entry it has not written before to the logger ``fritzexporter.event_log``, one line per
entry. Whatever collects the exporter's output (Loki, journald, Elasticsearch, ...) then
keeps the history. The feature adds no metrics and is off by default, because the lines
contain user names and addresses of your network. The exporter's log level must be
``INFO`` or lower for the lines to appear.

.. code-block:: text

    event_time=2026-10-09T21:49:06+02:00 group=sys id=504 msg="Anmeldung des Benutzers exporter an der FRITZ!Box-Benutzeroberfläche von IP-Adresse 192.0.2.10."

* ``id`` is the box's message type, ``group`` is ``sys``, ``net``, ``wlan`` and so on. The
  ``msg`` text is in the language of the box, so match on ``id`` rather than on the text.
* The box writes its own local time without a time zone. ``event_time`` is that time as
  ISO 8601 with the UTC offset that applied at that moment, taken from the time zone rule
  the box reports (``Time:GetInfo``). If the box does not report a usable time zone, the
  current offset is used, or no offset at all. A time in the hour that occurs twice when
  daylight saving time ends is resolved to the first occurrence. The log timestamp of the
  line itself is when the exporter first saw the entry, so the first collection after a
  start stamps the whole buffer with the current time; ``event_time`` is the real one.
* ``msg`` is quoted, with newlines and other control characters escaped, so that an entry
  cannot add or split lines. The ``q=`` token of a DynDNS update URL is replaced by
  ``q=<redacted>``.
* Entries are de-duplicated in memory, by time and message, and counted, so identical
  entries in the same second are each written. After the exporter restarts it reads the
  box's buffer again and entries it wrote before can appear a second time; while it runs,
  no entry is dropped. The box folds a repeated message into one row (``... [2 Meldungen
  seit 09.10.26 21:48:36]``); the folded row has a new time and counts as a new entry.
* If the box does not offer the action, the exporter logs one warning and stops reading the
  log for that device. A failed request, an error status or a body that is not an event log
  (for example while the box restarts) is logged once per failure streak and retried on
  the next collection. None of these fails a scrape. The warnings never contain the log
  URL, which carries a session id.
