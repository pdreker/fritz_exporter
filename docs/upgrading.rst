Upgrade Notes (potentially breaking changes)
============================================

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
