Code Documentation
==================

General structure
-----------------

fritz_exporter uses some "black magic" (this may be clever, but it makes understanding what's going on a lot harder...) to manage capabilities of devices. This section will try to make some sense of this code... At least for the author...

Top Level and Entry Point
-------------------------

The Top-Level is formed by a single ``FritzCollector`` object.

``FritzCollector`` holds an attribute ``self.capabilities`` of type ``FritzCapabilities``. ``FritzCapabilities`` is an iterable, dict-like object which holds a collection of ``FritzCapability`` (note plural vs. singular...).

When ``FritzCapabilities`` is intsnatiated it can be done, with or without a ``FritzDevice``. If done without a device, the resulting object will simply be a list of all capabilities. If done **with** a device, the resulting full list of capabilities will be checked against what the device actually supports and unsupported capabilities will be removed from the list.

When a device definition is found in the config, this will instantiate a ``FritzDevice``object and register it to the ``FritzCollector``.

``FritzCapability`` is an abstract baseclass (ABC) for the concrete capabilities. The subclasses automatically register to the baseclass using ``cls.__init_subclass__()``.

In the end the ``FritzCollector`` will have a ``FritzCapabilities`` representing the union of all supported capabilities of the devices. When ``FritzCollector.collect()``is called it will use this list of generally available capabilities to fetch the metrics from the ``FritzCapability`` baseclass, which will in turn call this for each individual device.

Yes, this is convoluted

tl;dr
-----

``FritzCollector`` has a list of ``FritzDevice``s and a ``FritzCapabilities`` collection of ``FritzCapability`` subclasses, which represent the union of all capabilities which are available on any registered device.

Each ``FritzDevice`` again has a ``FritzCapabilities`` collection representing its own supported capabilities which are then used to actually collect metrics.
