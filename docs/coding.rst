Code Documentation
==================

General structure
-----------------

fritz_exporter uses some "black magic" (this may be clever, but it makes understanding what's going on a lot harder...) to manage capabilities of devices. This section will try to make some sense of this code... At least for the author...

Module / File Map
-----------------

A quick orientation before diving in:

.. code-block:: text

    fritzexporter/
      __main__.py           – CLI entry point and main loop (parse_cmdline, main)
      fritzdevice.py        – FritzDevice, FritzCollector, DeviceSpec, FritzCredentials
      fritzcapabilities.py  – FritzCapability (ABC) + all concrete capability classes
                              + FritzCapabilities container
      reply.py              – Typed reading of device replies (Reply, read_action,
                              Issues, DataError), see "Reading Device Data"
      tr064_remote.py       – TR-064 connection setup: ports, TLS, timeouts, the
                              /tr064 prefix for WAN remote access
      fritz_webui.py        – Web interface client: login, data.lua, REST API, AHA
      fritz_aha.py          – Parser for the AHA (smart home) device list XML
      fritz_docsis.py       – Parser for the DOCSIS cable channel page (docInfo)
      fritz_fiber.py        – Parser for the fibre optical level page (fiberStats)
      fritz_rest_generic.py – Parsers for REST API connections and segment monitor
      exceptions.py         – Top-level exceptions
      config/
        config.py           – ExporterConfig, DeviceConfig (attrs @define classes)
                              + get_config() factory
        exceptions.py       – Config-specific exceptions


Startup Flow
------------

The entry point is ``fritzexporter/__main__.py`` → ``main()``.

1. A ``FritzCollector`` is created (empty, no devices yet).
2. Configuration is loaded via ``get_config()``, which reads either a YAML file or
   environment variables and returns an ``ExporterConfig`` instance.
3. For each ``DeviceConfig`` in the config, ``_register_device()`` builds a
   ``DeviceSpec`` (credentials, name, options) and hands it to
   ``fritzcollector.add_device()``, the one place where devices are set up.  It
   instantiates a ``FritzDevice``, which connects to the physical device via
   ``fritzconnection``, reads its serial number and model, and probes which
   capabilities it supports (see below).
4. A device that was set up is registered.  A device that could not be set up —
   unreachable, wrong credentials, no capabilities, or no readable serial number — is
   kept as an ``OfflineDevice`` with its ``DeviceSpec`` instead, logged once without a
   traceback: it is reported as ``fritz_device_reachable 0`` and set up again in the
   background, started by each scrape, until it works.  A device that answers but gives no readable serial number is given up after
   ``SERIAL_RETRIES`` (3) retries, with an error in the log; it stays reported as
   unreachable until the exporter is restarted.  A warning is logged whenever the
   reason an offline device fails changes.
5. The ``FritzCollector`` is registered with the Prometheus ``REGISTRY``.  Its
   ``describe()`` returns nothing, so registering does not run a scrape to learn the
   metric names.
6. An HTTP server is started (``prometheus_client.start_http_server``), and the main
   thread waits forever; scrapes run in the HTTP server's threads.

When Prometheus scrapes ``/metrics``, ``FritzCollector.collect()`` is called.  It holds
a re-entrant lock (``threading.RLock``) for the whole scrape, so concurrent scrapes do
not interfere.  A scrape that cannot get the lock within ``SCRAPE_LOCK_TIMEOUT``
(30 s) returns no data and is counted in ``fritz_scrapes_timeouts_total``.  Offline
devices are set up again in one background thread (``fritz-offline-retry``), which a
scrape starts unless it is still running; the scrape does not wait for it, so a device
that takes its connection timeout to fail holds up no scrape.


Configuration System
--------------------

``config/config.py`` provides two ``attrs`` ``@define`` classes:

``DeviceConfig``
    Represents one Fritz! device.  Fields: ``hostname``, ``username``, ``password``,
    ``password_file``, ``name``, ``host_info``, ``wifi_client_info``,
    ``connection_timeout``, ``use_tls``, ``port``, ``remote_access``,
    ``allow_unsafe_md5_password``.  Hostname is
    lowercased automatically via an attrs converter, and the name defaults to it.
    Validators check that a password or password file is given, password length,
    that any password file actually exists, the port range, and that
    ``remote_access`` is only set together with ``use_tls``.

``ExporterConfig``
    Top-level exporter configuration.  Fields: ``exporter_port`` (default 9787),
    ``log_level``, ``listen_address``, ``devices`` (list of ``DeviceConfig``).

``get_config(path)`` delegates to either ``_read_config_file()`` (YAML) or
``_read_config_from_env()`` (environment variables) and then calls
``ExporterConfig.from_config(raw_dict)``.  Both paths hand on only the keys that are
set, so the defaults live in one place, the field definitions.  ``from_config``
refuses keys the class has no field for, and duplicate device names.

Both config classes use ``attrs`` validators and converters — field values are
validated and coerced at construction time.  Every config problem raises a
``ConfigError`` (``config/exceptions.py``) whose message names the device and the
key; ``main()`` logs it as one line and exits.


The Capability System
---------------------

This is the "black magic" part.  Read carefully.

**Auto-registration of subclasses**

``FritzCapability`` is an abstract base class (ABC) defined in ``fritzcapabilities.py``.
Every concrete capability (e.g. ``DeviceInfo``, ``WanDSLInterfaceConfig``) is a
subclass of it.  The ``__init_subclass__`` hook fires automatically when Python loads
each subclass definition and appends the new class to the class-level list
``FritzCapability.subclasses``.  This means **no manual registration is needed** — just
define the subclass and it is discovered automatically.

**FritzCapabilities (plural) — the container**

``FritzCapabilities`` is a dict-like wrapper around ``{class_name: instance}`` for
every known ``FritzCapability`` subclass.  It can be constructed in two modes:

* **Without a device** — instantiates all subclasses but leaves every instance's
  ``present`` flag as ``False``.  Used by ``FritzCollector`` to drive the collect loop
  and hold the metric families.
* **With a device** — same construction, then immediately calls ``check_present()``
  which runs ``check_capability()`` on every instance against that specific device.

**Capability probing (check_capability)**

``FritzCapability.check_capability(device)`` performs a *two-stage* check:

1. **Static check**: each capability declares a ``self.requirements`` list of
   ``(service, action)`` tuples.  The method verifies that each service exists in
   ``device.fc.services`` and that the action is listed in that service's actions dict.
2. **Live call check**: even if the static check passes, some Fritz! devices advertise
   services they do not actually support.  The method therefore tries to *call* each
   requirement and, if ``FritzServiceError``, ``FritzActionError``, or similar
   exceptions are raised, it marks the capability ``present = False`` again.

Some capabilities replace this check: ``HostInfo`` and ``WlanAssociatedDevices`` are
only present when enabled in the device config (``host_info``, ``wifi_client_info``),
``WanDocsisCable`` is detected by the WAN access type, and ``WlanConfigurationInfo``
checks each WLAN band on its own.

The ``present`` flag of each ``FritzCapability`` instance reflects whether *that*
device supports *that* capability.

**The collect loop**

When ``FritzCollector.collect()`` runs:

1. The background retry of offline devices is started, unless it is still running or
   every offline device was given up.  Devices that the previous retry set up are
   registered now, so they are collected one scrape after they come back.
2. ``FritzDevice.add_connection_mode()`` adds each device's sample to one
   ``fritz_connection_mode`` gauge (DSL/fibre/mobile/cable/offline).
3. The collector holds its own ``FritzCapabilities`` instance, built without a device:
   one instance of every capability class, used only to drive the loop and to hold the
   metric families.  For each capability ``capa`` in it, ``capa.get_metrics(devices,
   name)`` is called:

   a. ``capa.create_metrics()`` re-creates the Prometheus metric families, clearing
      the values of the previous scrape.
   b. It iterates over **every registered device** and calls
      ``capa._generate_metric_values(device)`` only when
      ``device.capabilities[name].present`` is ``True`` for that device.  Each call is
      guarded, see *Reading Device Data* below.
   c. ``capa._get_metric_values()`` yields all its metric families (``self.metrics``).

4. The self-monitoring metrics follow: ``fritz_scrapes_total``,
   ``fritz_scrapes_timeouts_total``, ``fritz_scrapes_failed_total``,
   ``fritz_device_reachable`` and ``fritz_read_problem``.

All device data is collected before anything is yielded, so the availability reported
in ``fritz_device_reachable`` matches the data of the same scrape.

The indirection — one capability instance per class drives the loop, the per-device
capability flags decide whether values are fetched — is the key to understanding the
flow.

tl;dr
-----

``FritzCollector`` has a list of ``FritzDevice``\s and its own ``FritzCapabilities``
collection with one instance of every capability class.

Each ``FritzDevice`` has its **own** ``FritzCapabilities`` collection recording which
capabilities *that* device actually supports (and, per capability, the current read
problem, see below).

During ``collect()``, the collector's set drives iteration; the per-device sets gate
whether values are fetched from each device.


Reading Device Data
-------------------

Every value taken from a device — a TR-064 action result, a ``data.lua`` or REST API
JSON object, an element of the AHA XML — is read through ``fritzexporter/reply.py``,
never by indexing the raw data:

* ``read_action(device, service, action, **arguments)`` calls a TR-064 action and
  returns a ``Reply``, a read-only typed view on the result.  ``Reply(raw, source)``
  wraps a JSON object the same way.
* ``reply.num(key)``, ``reply.integer(key)``, ``reply.flag(key)`` and
  ``reply.text(key, default)`` read a required value; the ``opt_*`` variants return
  ``None`` instead.  ``reply.opt_obj(key)`` and ``reply.items(key)`` read nested
  objects and lists; ``issues.objects(reply, key)`` walks a list of objects.
  ``to_float``/``to_int``/``to_bool``/``to_str`` do the same for single values, e.g.
  list items or XML text.

One rule applies everywhere: a value that is missing, ``None``, ``""``, ``[]`` or ``{}``
is *absent* (empty containers, because JSON encoders for Lua — which the web interface
is written in — send an empty list as ``{}`` and vice versa)
(required reads raise, optional reads return ``None``); a value that is present but
not of the requested type raises ``DataError``.  Numbers come back as ``int``/``float``
and labels as ``str``.  A ``DataError`` names the source and field, never the value.

Failures stay at the level they affect
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

A failure should cost only what depends on it, and must move up a level when that
level depends on it.  Prometheus handles gaps; the exporter logs each one, so a gap
in a graph can be matched to a log line.

+------------+----------------------------------------+-----------------------------------+
| Level      | Example                                | Effect                            |
+============+========================================+===================================+
| Value      | the MER of one DOCSIS channel is "abc" | only the samples built from it    |
+------------+----------------------------------------+-----------------------------------+
| Item       | a host, channel or WLAN band has an    | only that item                    |
|            | unreadable label                       |                                   |
+------------+----------------------------------------+-----------------------------------+
| Capability | the reply is not an object, a list is  | that capability of that device in |
|            | not a list, or a value the whole       | this scrape (no partial set)      |
|            | capability depends on                  |                                   |
+------------+----------------------------------------+-----------------------------------+
| Capability | a bug in the exporter (``KeyError``,   | as above, logged with a traceback |
|            | ``TypeError``, ...)                    | on every scrape                   |
+------------+----------------------------------------+-----------------------------------+
| Device     | the device answers, but its serial     | registered offline; given up      |
|            | number cannot be read                  | after ``SERIAL_RETRIES`` (3)      |
|            |                                        | retries                           |
+------------+----------------------------------------+-----------------------------------+
| Capability | the device answers a TR-064 request    | that capability of that device in |
|            | with an error (``FritzActionError``,   | this scrape                       |
|            | ...)                                   |                                   |
+------------+----------------------------------------+-----------------------------------+
| Device     | the device does not answer             | unreachable for this scrape       |
|            | (``tr064_remote.TRANSPORT_ERRORS``)    |                                   |
+------------+----------------------------------------+-----------------------------------+

How the levels are built:

* **Value**: read through the ``Issues`` collector of the capability, ``self.issues``:
  ``self.issues.num(reply, key)``, ``opt_num``, ``num_or``, ``opt_integer``, ``flag``,
  ``equals``, ``text``, ``convert``.  They behave like the ``Reply`` methods but record a
  ``DataError`` and return ``None``; ``self._add(metric, labels, value)`` adds no
  sample for ``None``.
* **Item**: wrap the item in ``with self._item("host 3"):`` (in a capability) or
  ``with issues.item(...)`` (in a parser).  A ``DataError`` inside skips the rest of
  the block and drops the samples it already added.  ``issues.objects(reply, key)``
  skips list elements that are no objects.
* **Capability**: anything read strictly (``reply.num()``, ``reply.text()`` without
  ``self.issues``) outside an item.  Read a value strictly when the capability depends
  on it, e.g. a label shared by all its samples or a count that drives a loop.
* **Device**: ``FritzDevice.get_device_info`` raises ``FritzDeviceSerialUnknownError``;
  the device is not collected, because the serial is the label that tells devices
  apart (names are unique only among the configured devices).

``FritzCapability.get_metrics`` guards each device's ``_generate_metric_values`` call.
In every case below, the samples that device added to the capability are dropped, so
no partial set is exported:

* a connection error marks the device unavailable for this scrape;
* a ``FritzWebUiError`` or ``DataError`` skips the capability for this scrape;
* any other error (``KeyError``, ``TypeError``, ...) is a bug, since all reads go
  through ``reply.py``: it is contained the same way, so it does not break the scrape,
  but logged with a traceback on every scrape.

Before samples are kept, they are checked to be renderable (numeric value, ``str``
labels), because ``prometheus_client`` converts values only while rendering the
scrape.

**Reporting.** Each device and capability keeps its current problem level
(``partial``, ``skipped`` or ``error``):

* ``fritz_read_problem{capability, level}`` is ``1`` while a problem lasts, so a gap in
  a graph can be explained from Prometheus alone, even weeks later.  It is exported only
  for reachable devices (for the others, nothing was read).  ``get_connection_mode``
  reports its problems the same way, as ``capability="ConnectionMode"``;
  ``report_problem()`` does the bookkeeping for both;
* a warning is logged when a problem appears or changes level, a reminder with the
  start time every hour (``PROBLEM_REMINDER_INTERVAL``) while it lasts, the details at
  debug level on the other scrapes, and an info line when it is gone. Comparing the
  level, not the text, keeps changing item indexes from causing new warnings.

A capability does not catch errors itself, except where it can do better than skip,
e.g. the fibre levels fall back to the TR-064 values.


Adding a New Metric / Capability
---------------------------------

1. Open ``fritzexporter/fritzcapabilities.py``.
2. Create a new subclass of ``FritzCapability``.  The name of the class becomes the key
   used to look up the capability in every ``FritzCapabilities`` dict.
3. In ``__init__``, call ``super().__init__()`` and then populate
   ``self.requirements`` with the ``(service, action)`` tuples the metric requires.
   These are TR-064 service/action pairs as exposed by ``fritzconnection``.
4. Implement ``create_metrics(self)`` — create the metric families with
   ``self._gauge(key, name, help, *labels, unit=...)`` for current values and
   ``self._counter(...)`` for monotonically increasing counters; both put the
   ``serial`` and ``friendly_name`` labels first.  Name new metrics
   by the `Prometheus naming rules <https://prometheus.io/docs/practices/naming/>`_
   (base units as a suffix via ``unit=``, ``_total`` for counters, ratios 0–1); existing
   names are kept as they are for compatibility, even where they predate these rules.
5. Implement ``_generate_metric_values(self, device)`` — call the TR-064 actions via
   ``read_action(device, service, action)`` and add samples with
   ``self._add(key, labels, self.issues.num(reply, field))``, keeping failures at
   their level (see *Reading Device Data*).
6. If the capability reads the web interface, set ``uses_webui = True`` and get the
   client with ``self._webui(device)``; if a config option switches it on, override
   ``enabled(device)``; if a requirement needs arguments to be probed, override
   ``_probe()``.  Tables read by index go through ``self._enumerate()``.
7. Add tests in ``tests/test_fritzcapabilities.py`` using the mock infrastructure in
   ``tests/fc_services_mock.py``; ``collect_device()`` in ``tests/collect.py`` sets
   up one fake device and scrapes it.  No further registration is needed; the subclass is
   discovered automatically via ``__init_subclass__``.
8. Show the new metrics in the overview dashboard, see *Grafana Dashboards* below.


Grafana Dashboards
------------------

``misc/grafana/build_dashboards.py`` writes the two dashboards in ``misc/grafana``
(classic dashboard JSON, Grafana 10 and later).  Edit the script, never the JSON, and
run it::

    uv run python misc/grafana/build_dashboards.py

``tests/test_grafana_dashboards.py`` fails when the JSON is not what the script
writes, when a metric family of the exporter is missing from the overview, when a
query does not select the device (``friendly_name="$device"``), when a query uses a
label or label value that the demo exporter's boxes do not send, or when panels
overlap.

To look at the dashboards, run Prometheus and Grafana (e.g. in containers) and let
Prometheus scrape your exporter and ``misc/grafana/demo_exporter.py``.  The demo
exporter serves a cable and a fibre box with smart home devices, built from the test
fixtures, with values that change over time, so the panels for hardware you do not
have show data::

    uv run python misc/grafana/demo_exporter.py 9788


Supporting Modules
------------------

**reply.py**

Typed reading of every device reply and the ``Issues`` collector for the value and item
level; see *Reading Device Data*.

**tr064_remote.py**

Creates the ``FritzConnection`` for a device (``create_fritz_connection``) from its
``ConnectionOptions``: TR-064 and web interface ports, TLS, the per-request timeout
(fritzconnection's own HTTP calls have none), and, for WAN remote access, a session
that prefixes TR-064 paths with ``/tr064``.

**fritz_webui.py**

``FritzWebUiClient``, the client for everything only the web interface offers: the
challenge-response login (PBKDF2; the legacy MD5 login only with
``allow_unsafe_md5_password``), ``data.lua`` pages, the ``/api/v0`` REST API and the
AHA smart home interface.  All requests go through ``_request``, which reads at most
``MAX_RESPONSE_BYTES`` (4 MiB) of a reply.  Errors are raised as ``FritzWebUiError``
with texts that never contain the session id.  The client logs in again only when the
device rejects the session (HTTP 401/403, or the HTML login page instead of JSON).
Once a request finds the device not answering, the other web interface requests of
the scrape fail at once (``start_scrape()`` resets this).  A device that offers only
the MD5 login, which is not allowed for it, gets no web interface requests until the
exporter is restarted.

**fritz_aha.py, fritz_docsis.py, fritz_fiber.py, fritz_rest_generic.py**

Parsers for the web interface replies: the AHA device list (XML, via ``defusedxml``),
the DOCSIS channel page, the fibre optical level history and the REST API connection
and segment monitor endpoints.  Each turns a raw reply into typed data through
``reply.py`` and takes an ``Issues`` collector, so a defective value or item is
skipped and recorded instead of failing the whole capability.

**exceptions.py**

Defines ``FritzDeviceHasNoCapabilitiesError``, raised when a device is connected
successfully but no capabilities are detected, and ``FritzDeviceSerialUnknownError``,
raised when its serial number cannot be read.  In both cases the device is registered
offline.  Config-specific exceptions are in ``config/exceptions.py``, web interface
exceptions in ``fritz_webui.py`` and ``DataError`` in ``reply.py``.
