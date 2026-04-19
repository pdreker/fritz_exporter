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
      fritzdevice.py        – FritzDevice, FritzCollector, FritzCredentials
      fritzcapabilities.py  – FritzCapability (ABC) + all concrete capability classes
                              + FritzCapabilities container
      fritz_aha.py          – XML helper for AHA (smart home) device data
      action_blacklists.py  – TR-064 service/action pairs that must never be called
      data_donation.py      – "donate-data" CLI mode: collect & upload device data
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
3. For each ``DeviceConfig`` in the config, a ``FritzDevice`` is instantiated.  During
   construction ``FritzDevice`` connects to the physical device via ``fritzconnection``
   and immediately probes which TR-064 capabilities it supports (see below).
4. Each ``FritzDevice`` is registered with ``fritzcollector.register()``, which also
   merges the device's capabilities into the collector's global capability set.
5. The ``FritzCollector`` is registered with the Prometheus ``REGISTRY``.
6. An HTTP server is started (``prometheus_client.start_http_server``), and the process
   enters an ``asyncio`` event loop that runs forever.

When Prometheus scrapes ``/metrics``, ``FritzCollector.collect()`` is called.  This
method is protected by a re-entrant lock (``threading.RLock``) so concurrent scrapes
do not interfere with each other.


Configuration System
--------------------

``config/config.py`` provides two ``attrs`` ``@define`` classes:

``DeviceConfig``
    Represents one Fritz! device.  Fields: ``hostname``, ``username``, ``password``,
    ``password_file``, ``name``, ``host_info``.  Hostname is lowercased automatically
    via an attrs converter.  Validators check password length and that any password
    file actually exists.

``ExporterConfig``
    Top-level exporter configuration.  Fields: ``exporter_port`` (default 9787),
    ``log_level``, ``listen_address``, ``devices`` (list of ``DeviceConfig``).

``get_config(path)`` delegates to either ``_read_config_file()`` (YAML) or
``_read_config_from_env()`` (environment variables) and then calls
``ExporterConfig.from_config(raw_dict)``.

Both config classes use ``attrs`` validators and converters — field values are
validated and coerced at construction time.  Config-specific exceptions live in
``config/exceptions.py``.


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
  ``present`` flag as ``False``.  Used by ``FritzCollector`` to hold the global,
  device-agnostic union of capabilities.
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

The ``present`` flag of each ``FritzCapability`` instance reflects whether *that*
device supports *that* capability.

**Merging capabilities across devices**

When a second (or third…) device is registered via ``FritzCollector.register()``,
``FritzCapabilities.merge()`` is called on the collector's global capability set.
Merge uses a logical OR: a capability in the global set becomes ``present = True`` if
*any* registered device supports it.

This is what allows the collector to serve a union of all capabilities without needing
to know in advance which devices are attached.

**The collect loop**

When ``FritzCollector.collect()`` runs:

1. It iterates over the **global** ``FritzCapabilities`` (one entry per capability
   class, ``present`` meaning "at least one device supports it").
2. For each capability ``capa``:

   a. ``capa.create_metrics()`` is called — this re-initialises the Prometheus metric
      family objects, clearing any values from a previous scrape.
   b. ``capa.get_metrics(devices, name)`` is called.  Inside, it iterates over **every
      registered device** and calls ``capa._generate_metric_values(device)`` only when
      ``device.capabilities[name].present`` is ``True`` for that device.  This is how
      per-device capability support is respected even though iteration happens at the
      global level.
   c. After all devices have been processed, ``capa._get_metric_values()`` yields the
      now-populated metric families.

The indirection — global capabilities iterate, but per-device capability flags gate
whether values are actually fetched — is the key insight needed to understand the flow.

Additionally, ``FritzDevice.get_connection_mode()`` is called per device *before* the
capability loop to emit a special ``fritz_connection_mode`` gauge that detects
DSL/mobile/offline state.

tl;dr
-----

``FritzCollector`` has a list of ``FritzDevice``\s and a **global** ``FritzCapabilities``
collection representing the *union* of all capabilities supported by any registered device.

Each ``FritzDevice`` has its **own** ``FritzCapabilities`` collection recording which
capabilities *that* device actually supports.

During ``collect()``, the global set drives iteration; the per-device sets gate whether
values are fetched from each device.


Adding a New Metric / Capability
---------------------------------

1. Open ``fritzexporter/fritzcapabilities.py``.
2. Create a new subclass of ``FritzCapability``.  The name of the class becomes the key
   used to look up the capability in every ``FritzCapabilities`` dict.
3. In ``__init__``, call ``super().__init__()`` and then populate
   ``self.requirements`` with the ``(service, action)`` tuples the metric requires.
   These are TR-064 service/action pairs as exposed by ``fritzconnection``.
4. Implement ``create_metrics(self)`` — create the Prometheus metric family objects and
   store them in ``self.metrics[key]``.  Use ``GaugeMetricFamily`` for current values
   and ``CounterMetricFamily`` for monotonically increasing counters.
5. Implement ``_generate_metric_values(self, device)`` — call the TR-064 actions via
   ``device.fc.call_action(service, action)`` and populate the metric families using
   ``self.metrics[key].add_metric(labels, value)``.
6. Implement ``_get_metric_values(self)`` — ``yield`` each metric family from
   ``self.metrics``.
7. Add tests in ``tests/test_fritzcapabilities.py`` using the mock infrastructure in
   ``tests/fc_services_mock.py``.  No further registration is needed; the subclass is
   discovered automatically via ``__init_subclass__``.


Supporting Modules
------------------

**action_blacklists.py**

Defines ``call_blacklist``, a list of ``BlacklistItem(service, action)`` tuples
representing TR-064 calls that must *never* be made by the exporter.  These include
calls that retrieve persistent configuration data, security keys, WEP keys, phone
book entries, and other sensitive or potentially destructive operations.  The blacklist
is used exclusively by the *data donation* feature.

**fritz_aha.py**

Provides ``parse_aha_device_xml(xml_string)``, a small helper that parses the AHA
(AVM Home Automation) XML format returned for smart home devices.  It extracts battery
level and low-battery indicator fields.  ``defusedxml`` is used instead of the standard
library ``xml`` module to prevent XML injection attacks.

**data_donation.py**

Implements the ``--donate-data`` / ``--upload-data`` CLI mode.  When active, the
exporter connects to the device, iterates over *all* services and ``Get*`` actions
(excluding per-index/per-IP lookups), calls each one, sanitises sensitive fields
using a built-in blacklist plus any user-specified ``--sanitize`` arguments, and either
prints or uploads the resulting JSON blob to the project's collection endpoint.  This
data helps the project discover which TR-064 actions are available on different device
models.

**exceptions.py**

Defines ``FritzDeviceHasNoCapabilitiesError``, raised when a device is connected
successfully but no TR-064 capabilities are detected.  Config-specific exceptions are
in ``config/exceptions.py``.
