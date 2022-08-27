# Fritz! exporter for prometheus

This is a prometheus exporter for AVM Fritz! home network devices commonly found in Europe. This exporter uses the devices builtin TR-064 API via the fritzconnection python module.

The exporter should work with Fritz!Box and Fritz!Repeater Devices (and maybe others). It actively checks for supported metrics and queries the for all devices configured (Yes, it has multi-device support for all you Mesh users out there.)

It has been tested against an AVM Fritz!Box 7590 (DSL), a Fritz!Repeater 2400 and a Fritz!WLAN Repeater 1750E. If you have another box and data is missing, please file an issue or PR on GitHub.

## Metrics

The following groups of metrics are currently available:

* Base Information (Model, Serial, Software Version, Uptime)
* Software Information (Update available)
* LAN statistics (Ethernet only)
* WAN statistics
* DSL statistics
* PPP statistics
* WiFi statistics
* WAN Layer1 (physical link) statistics

There is code in `fritzexporter/fritzcapabilities.py` to extract information about ever single host the device knows about but it is completely disabled as of now as polling the information can take 20-30 seconds depending on your network setup. If you want to test this out simply uncomment that code and rebuild the docker image.

If there is any information missing or not displayed on your specific device, please open an issue.

## Known problems

* It seems like Fritz!OS does not internally count the packets for the Guest WiFi. So even though those counters are there they are always 0. This seems to be a problem with Fritz!OS and not the exporter. The counters are delivered nontheless, just in case this gets fixed by AVM.
* If you receive `Fatal Python error: init_interp_main: can't initialize time` when running the container you may have to update libseccomp on your Docker host. This issue mainly happens on Raspberry Pi and is triggered by a version of libseccomp2 which is too old. See <https://askubuntu.com/questions/1263284/apt-update-throws-signature-error-in-ubuntu-20-04-container-on-arm> (Method 2) and <https://github.com/pdreker/fritzbox_exporter/issues/38>.
* On some boxes LAN Packet counters are stuck at 0 even though the box reports the stats as available.
* Fritz!OS does not allow passwords longer than 32 characters (as of 07.25). If you try to use a longer password, the admin ui will discard all characters after the 32nd. The UI will also cut your inserted password down to 32 characters. So you will be able to login in the UI with the long password. The exporter however does not alter your password and requests will result in a 401 Unauthorized. So please be aware of this limit and choose a suitable password.

## Grafana Dashboard

There is a simple Grafana dashboard avaliable at <https://grafana.com/grafana/dashboards/13983>

## Upgrade Notes (potentially breaking changes)

### v2.0.0

#### Label changes

Version 2.0.0 changes the `direction` labels of some metrics to consistently use `tx` (transmitted, upstream) and `rx` (received, downstream). Before this change the labels were `up` and `down` respectively, while other metrics used `tx`and `rx`.

Affected metrics:

* fritz_wan_data
* fritz_wan_data_packets

#### Config changes

Multi device configuration was dropped from the environment configuration. The `FRITZ_EXPORTER_CONFIG` environment variable was removed completely. When using environment configuration this exporter now only supports a single device. For multi device support please use the new config file option.

#### WiFi metrics changes

All WiFi metrics have been merged. So e.g. `fritz_wifi_2_4GHz_*` is changed to `fritz_wifi_*` and two labels (wifi_index and wifi_name) are added to the metrics.

### v1.0.0

Version 1.0.0 of the exporter has completely reworked how this exports metrics! If you have used this exporter in the past, you will get a completely new set of metrics.

* the metrics prefix has changed from `fritzbox_` to `fritz_`
* all labels have been converted to lower-case (e.g. `Serial` -> `serial`) to be in line with the common usage of labels
* some metrics have been renamed to better reflect their content

## Helping out

If your device delivers some metrics which are not yet scraped by this exporter you can either create a Pull Request, which will be gladly accepted ;-)

Alternatively you can use the following commands and the litlle helper script in the root of this repository to let me know of metrics:

```bash
fritzconnection -i <FRITZ-IP> -s # Lists available services
fritzconnection -i <FRITZ-IP> -S <ServiceName> # Lists available action for a service
python -m fritz_export_helper <FRITZ-IP> <USERNAME> <PASSWORD> <ServiceName> <ActionName> # Will output the data returned from the device in a readable format
```

If you have found something you need/want, open an issue provide the following infos:

1. Model of Device (e.g. FritzBox DSL 7590)
2. ServiceName and ActionName
3. Output from fritz_export_helper (make sure, there is not secret data, like password or WiFi passwords in there, before sending!)
4. What output you need/want and a little bit of info of the environment (Cable, DSL, Hybrid LTE Mode, ... whatever might be relevant)

## Disclaimer

Fritz! and AVM are registered trademarks of AVM GmbH. This project is not associated with AVM or Fritz other than using the devices and their names to refer to them.

## Copyright

Copyright 2019-2022 Patrick Dreker <patrick@dreker.de>

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

  <http://www.apache.org/licenses/LICENSE-2.0>

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
