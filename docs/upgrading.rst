Upgrade Notes (potentially breaking changes)
============================================

v4.0.0
------

Legacy MD5 web login disabled by default
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Devices on Fritz!OS before 7.24 only offer an MD5 login to the web interface,
which the smart home, DOCSIS and REST API metrics use. The exporter now refuses
that login by default and exits with a critical log message at the first scrape.
To keep scraping such a device, upgrade Fritz!OS or set
``allow_unsafe_md5_password: true`` (env: ``FRITZ_ALLOW_UNSAFE_MD5_PASSWORD``) for
it. See :ref:`md5-login`.

Earlier versions did not ask the device for the PBKDF2 login, which Fritz!OS 7.24 and
later only offer on request, and so used the MD5 login on every firmware. They now ask
for it, so devices on Fritz!OS 7.24 and later log in with PBKDF2 without any change
on your side.

After a refused web login (wrong credentials, or logins blocked by the device) the
exporter now waits at least a minute, or as long as the device blocks logins, before
the next attempt. It used to try again for every web interface metric group, which
made the device block logins longer, also for the browser.

Devices without a readable serial number are not collected
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The serial number is the label that tells devices apart (names may repeat). A
device that answers but gives no readable serial number is now reported as
unreachable (``fritz_device_reachable 0``) instead of being collected with
``serial="n/a"``. The exporter retries it three times, then gives up with an error
in the log until it is restarted. A missing serial in the device's reply used to stop
the exporter at startup.

Unreadable data is skipped and reported
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When a value, an item (a host, a channel, ...) or a whole reply cannot be read, the
exporter skips only that part and reports it in the new ``fritz_read_problem`` metric
and in the log. Before, such a value silently became a gap, or the whole scrape
failed. The log messages for failing web interface requests changed from
"Failed to fetch … data" to "Could not read <Capability> data from <host>,
skipping it: …"; while a problem lasts it is repeated as a reminder once an hour
instead of on every scrape.

Smart home values that are unknown are no longer reported as 0
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Fritz!OS leaves an element empty when the value is unknown. An empty
``<batterylow/>`` or ``<lock/>`` used to be exported as ``0`` ("battery fine",
"not locked"); it now gives no sample for ``fritz_ha_battery_low`` or
``fritz_ha_switch_lock``.

v3.5.0
------

Remote access uses one port for TR-064 and the web interface
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

With ``remote_access: true`` (env: ``FRITZ_REMOTE_ACCESS``), ``port`` now sets the
remote HTTPS port for both TR-064 and the web interface (smart home, DOCSIS and REST
API metrics). Without ``port`` the exporter now uses ``443`` for both; it used to
send TR-064 to ``49443``, which the device does not serve over remote access.

Local scrapes are not affected: ``port`` still sets only the TR-064 port there.
See :ref:`ports`.

Smart home (AHA) metrics now work over local TLS
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

With ``use_tls: true`` the exporter used to send smart home requests to the
device's remote access port, which fails on the LAN. When that happened it also
reported the device as unreachable and skipped the metrics collected after smart
home. It now sends them to port 443, and a failed smart home fetch no longer marks
the device unreachable.

v3.4.0
------

Default connection timeout added
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

``connection_timeout`` (env: ``FRITZ_CONNECTION_TIMEOUT``) now defaults to **10
seconds** instead of no timeout. The timeout is used for the initial TR-064
connection as well as every HTTP read done during a scrape (including smart-home
AHA calls), so a slow or wedged device can no longer block the exporter
indefinitely.

Setting ``connection_timeout: 0`` restores the previous behaviour (no timeout).

**Why:** the exporter used to block a scrape forever on a device that stopped
responding. Parallel scrapes (e.g. multiple Prometheus instances) would then
accumulate behind that stuck scrape and all stop receiving metrics until the
exporter was restarted. The new default bounds this so scrapes complete and
report the device as unreachable.

v3.0.0
------

Default listen address changed
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The default value of ``listen_address`` (env: ``FRITZ_LISTEN_ADDRESS``) has
changed from ``0.0.0.0`` to ``127.0.0.1``.

**Who is affected:** deployments that rely on the old default to expose the
exporter on all network interfaces — for example, a bare-metal install where
Prometheus scrapes the exporter over the network without an explicit
``listen_address`` setting.

**Who is not affected:** Docker/Kubernetes deployments that publish the port
at the container/pod level; any deployment that already sets
``FRITZ_LISTEN_ADDRESS`` or ``listen_address`` explicitly in the config file.

**What to do:** if you need the exporter to be reachable from another host,
set ``listen_address: 0.0.0.0`` in your config file or
``FRITZ_LISTEN_ADDRESS=0.0.0.0`` in your environment explicitly. Closes
`#402 <https://github.com/pdreker/fritz_exporter/issues/402>`_.

Home automation metric label change
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

All ``fritz_ha_*`` Prometheus metrics now include a populated ``device_id``
label. Previously this label was always emitted as an empty string ``""`` due
to a bug; it now carries the real ``NewDeviceId`` value from the Fritz!Box.

**Impact:** Prometheus treats a changed label set as a new time series. On
the first scrape after upgrading, all existing ``fritz_ha_*`` series will be
replaced by new ones, causing **counter resets**. Any dashboards or alerts
that filtered on ``{device_id=""}`` will stop matching.

**What to do:** update dashboards and alert rules to either remove the
``device_id`` filter or match the real device ID value. Counter-based panels
may show a reset spike on the first scrape; this is expected and will
stabilise on the next collection cycle.

v2.0.0
------

Label changes
^^^^^^^^^^^^^

Version 2.0.0 changes the ``direction`` labels of some metrics to consistently use ``tx`` (transmitted, upstream) and ``rx`` (received, downstream). Before this change the labels were ``up`` and ``down`` respectively, while other metrics used ``tx`` and ``rx``.

Affected metrics:

* fritz_wan_data
* fritz_wan_data_packets

Config changes
^^^^^^^^^^^^^^

Multi device configuration was dropped from the environment configuration. The ``FRITZ_EXPORTER_CONFIG`` environment variable was removed completely. When using environment configuration this exporter now only supports a single device. For multi device support please use the new config file option.

WiFi metrics changes
^^^^^^^^^^^^^^^^^^^^

All WiFi metrics have been merged. So e.g. ``fritz_wifi_2_4GHz_*`` is changed to ``fritz_wifi_*`` and two labels (``wifi_index`` and ``wifi_name``) are added to the metrics.

v1.0.0
------

Version 1.0.0 of the exporter has completely reworked how this exports metrics! If you have used this exporter in the past, you will get a completely new set of metrics.

* the metrics prefix has changed from ``fritzbox_`` to ``fritz_``
* all labels have been converted to lower-case (e.g. ``Serial`` -> ``serial``) to be in line with the common usage of labels
* some metrics have been renamed to better reflect their content
