# Monitoring Mixin for Fritz!Exporter

Yes, we are going full overkill with this ;-)

## What to monitor?

We are going with the USE mathod: Utilisation, Saturation, Errors

## Utilisation

The main problem here is, that we have all kinds of measurements, but often times we can't really
determine the potential maximum to determine a utilisation so we will have to come up with some
computed metrics to solve this problem.

### Metrics to watch

* WAN/DSL Link Utilisation (Up and Downstream)
  * for DSL we can see the maximum
* LAN Utilisation
  * may have to externally specify the limits
* Wifi Utilisation
  * maximum is hard to come by, as the box does not report it.

## Saturation

As we often do not know maximums saturation ist also difficult to come by. Especially since we
also do not have LAN Error counters (at least not currently using TR-064)

## Errors

The only obvious error counters we can get from the device are FEC and CRC error counters from
the DSL Link.
