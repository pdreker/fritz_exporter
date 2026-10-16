Upgrade Notes (potentially breaking changes)
============================================

v4.0.0
------

The first three sections need action from most users: dashboards and alerts that
use the moved labels, configs without ``name``, and Docker or Helm setups.

Addresses and names moved to ``_info`` metrics
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Labels that change while the thing they describe stays the same made a new series
each time: a reconnect, a new DHCP lease, a renamed host. They moved to a separate
metric with the value ``1``, which you can join on its key labels.

* **WAN connections:** ``fritz_wan_connection_uptime_seconds_total`` and
  ``fritz_wan_connection_status`` lose ``ip_address``. The address is in the new
  ``fritz_wan_connection_info{connection, connection_name, media_type, stack,
  ip_address}``, for every stack that has one.
* **Hosts** (``host_info: true``): ``fritz_host_active`` and ``fritz_host_speed`` are
  keyed by ``mac_address`` only; ``ip_address``, ``hostname``, ``interface``,
  ``port`` and ``model`` are in the new ``fritz_host_info``. Hosts without a MAC
  address (e.g. some VPN clients) are left out, since nothing tells them apart.

To keep a host's name in a query, join on the MAC address, e.g.::

  fritz_host_active * on (serial, mac_address) group_left (hostname) fritz_host_info

Stricter configuration
^^^^^^^^^^^^^^^^^^^^^^

The exporter now refuses to start, with one message that names the device and the
key, on:

* a key it does not know, at the top level or in a device (a typo such as
  ``pasword:`` used to be ignored, and the device then logged in without a
  password);
* a device without ``password`` or ``password_file`` in the config file;
* two devices with the same name. **The name now defaults to the hostname**, in the
  config file and for ``FRITZ_NAME``; it used to be empty in the config file and
  ``Fritz!Box`` from the environment. Without a ``name``, the ``friendly_name``
  label of your series changes, so set ``name`` (or ``FRITZ_NAME``) to keep it.

A config file that is not valid YAML, or of the wrong shape, gives that message
instead of a traceback.

Docker image and Helm chart
^^^^^^^^^^^^^^^^^^^^^^^^^^^

* The image runs as user ``nobody`` (65534) instead of root. **A mounted config
  file or password file must be readable by that user.** The Helm chart already ran
  the container as 65534.
* The image sets ``FRITZ_LISTEN_ADDRESS=0.0.0.0``, so a published port reaches the
  exporter with the environment configuration. With a config file, set
  ``listen_address: 0.0.0.0`` in it.
* The Helm chart puts ``listen_address: 0.0.0.0`` into the config secret it creates,
  unless ``config`` sets ``listen_address``. A secret of your own
  (``createConfigSecret: false``) must set it.
* The image installs the dependency versions from ``uv.lock``, checked against their
  hashes.

Legacy MD5 web login disabled by default
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Devices on Fritz!OS before 7.24 only offer an MD5 login to the web interface,
which the smart home, DOCSIS and REST API metrics use. The exporter now refuses
that login by default: it logs an error and reads no web interface metrics from
that device until it is restarted; its TR-064 metrics keep working. To get them
back, upgrade Fritz!OS or set
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

The exporter now logs in again only when the device rejects the session, not after
every failed request, and it stops sending web interface requests for the rest of a
scrape once the device does not answer one. Replies larger than 4 MiB are refused.

``--donate-data`` removed
^^^^^^^^^^^^^^^^^^^^^^^^^

The data donation (``--donate-data``, ``--upload-data``, ``--sanitize``) is gone. To
help with a device the exporter does not support yet, open an issue.

Devices without a readable serial number are not collected
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The serial number is the label that tells devices apart in Prometheus. A
device that answers but gives no readable serial number is now reported as
unreachable (``fritz_device_reachable 0``) instead of being collected with
``serial="n/a"``. The exporter retries it three times, then gives up with an error
in the log until it is restarted. A missing serial in the device's reply used to stop
the exporter at startup.

Only a device that does not answer is reported as unreachable
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Any TR-064 error used to report the device as unreachable
(``fritz_device_reachable 0``) for the scrape and drop the metrics collected after
it, even when the device answered, e.g. with "action not supported". Now only a
device that does not answer (refused, timed out, reset) is unreachable; an error
reply skips the metric group that got it, shown in ``fritz_read_problem``; one for a
single host, WiFi client or similar skips only that one. A failure to fetch the mesh
list only skips the mesh metrics.

``host_info: true`` now also works on a device with fewer than two hosts in its host
table; such a device used to fail its setup and was never collected.

Unreadable data is skipped and reported
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

When a value, an item (a host, a channel, ...) or a whole reply cannot be read, the
exporter skips only that part and reports it in the new ``fritz_read_problem`` metric
and in the log. Before, such a value silently became a gap, or the whole scrape
failed. See below for the new log messages.

Unknown values are no longer reported as 0
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

* **Fibre levels:** some fibre boxes answer 0 for both optical levels over TR-064;
  the exporter then reads them from the web interface. When that fails, it used to
  export ``0`` dBm; now ``fritz_fiber_optical_signal_level_dBm`` and
  ``fritz_fiber_transmit_optical_level_dBm`` have no sample, and the failure shows
  in ``fritz_read_problem{capability="WanFiberInterfaceConfig",level="partial"}``
  instead of a warning on every scrape.
* **DOCSIS channels** without a channel ID used to be exported as channel ``0``,
  several of them as the same series; they are now skipped and reported.
* A web interface reply without its data part (e.g. a ``docInfo`` page without
  ``data``) used to give no metrics without a word; it is now reported as a read
  problem. An empty data part still means "no data".
* **Smart home:** Fritz!OS leaves an element empty when the value is unknown. An empty
  ``<batterylow/>`` or ``<lock/>`` used to be exported as ``0`` ("battery fine",
  "not locked"); it now gives no sample for ``fritz_ha_battery_low`` or
  ``fritz_ha_switch_lock``.

Log messages reworked
^^^^^^^^^^^^^^^^^^^^^

Many log messages changed wording, level or frequency. **If you match log messages,
e.g. for alerts or in a log analysis tool, check your patterns.** For alerting,
consider the ``fritz_read_problem`` and ``fritz_device_reachable`` metrics instead,
which do not depend on wording.

* **Device names:** every message about a device names it the same way,
  "<name> (<host>)", e.g. "Fritz!Box 7690 (fritz.box)", or the host alone if the
  name is the host. Messages used to show the host, the serial number
  ("device serial …") or no device at all.
* **Skipped data** (replaces "Failed to fetch … data from …"):

  * "Could not read <Capability> data from <device>, skipping it: <reason>" — the
    whole capability was skipped for this scrape;
  * "Skipped part of the <Capability> data from <device>: <details>" — some values
    or items were skipped;
  * "Bug while collecting <Capability> data from <device>, …" — an error in the
    exporter, logged at ``ERROR`` with a traceback;
  * while a problem lasts, the warning is repeated once an hour as
    "…, still since <time> (<n> scrapes): …" instead of on every scrape (details at
    ``DEBUG`` in between), and "Reading <Capability> data from <device> works again"
    is logged at ``INFO`` when it is gone.

* **Devices that stay offline:** "Device <device> is still offline, now because:
  <reason>" at ``WARNING`` when the reason changes, and "Giving up on device <device>
  after <n> attempts: …" at ``ERROR``.
* **Web login:** "login not retried for <n> seconds after: <reason>" while the
  exporter waits after a refused login.
* **Devices that cannot be set up:** one error, "Failed to initialize device
  <device>, it will be reported as down: <reason>", without a traceback. It replaces
  "unable to connect to …", "Not authorized to get device info from …" (the reason
  now ends in "(check username and password)") and "Device <device> has no detected
  capabilities." (now a reason, and the exporter keeps running).
* **Unreachable during a scrape:** "Device <device> is unreachable, skipping
  <Capability> metrics for this collection cycle: <reason>" at ``WARNING``, without
  a traceback.

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
