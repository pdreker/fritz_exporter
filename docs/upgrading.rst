Upgrade Notes (potentially breaking changes)
============================================

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
