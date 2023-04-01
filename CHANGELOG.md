## [2.2.1](https://github.com/pdreker/fritz_exporter/compare/v2.2.1-pre.6...v2.2.1) (2022-12-29)



## [2.2.2](https://github.com/pdreker/fritz_exporter/compare/v2.2.1...v2.2.2) (2023-04-01)


### Bug Fixes

* **helm:** corrected secret to convert map to YAML string ([3814de2](https://github.com/pdreker/fritz_exporter/commit/3814de2b727670537f77a9cab36e786755a58ec5))
* **helm:** set interval to 60s ([d3f8854](https://github.com/pdreker/fritz_exporter/commit/d3f8854f5d423be8692ebf14b1413b11e647af20))
* **helm:** using selectorLabels var in ServiceMonitor ([6ecba54](https://github.com/pdreker/fritz_exporter/commit/6ecba54276e9cffbe1b20afc311d9240878adf55))
* remove need for _version.py ([434581b](https://github.com/pdreker/fritz_exporter/commit/434581ba027fd62d09b097af6c2f4fa813f294a5))
* sanitize (WANIPConnection1, GetInfo, NewExternalIPAddress) ([ebdf787](https://github.com/pdreker/fritz_exporter/commit/ebdf7874b0d3660b184e4d31342580c5114d564d))


### Documentation

* add link to dashboard also displaying host info metrics ([f6fd853](https://github.com/pdreker/fritz_exporter/commit/f6fd85381b4db79c436d5c8b0ff8f8e537ffea21))
* Add reference to PyPI in docs ([d53b03a](https://github.com/pdreker/fritz_exporter/commit/d53b03aa1fee864f3f8d539b90b933d7c2c5e5a8))
* fix badges in README ([685e5d2](https://github.com/pdreker/fritz_exporter/commit/685e5d20085c80d435fa232390593b7ec507d146))
* reduce warnings/caveats for "host_info" ([f614d5b](https://github.com/pdreker/fritz_exporter/commit/f614d5bd0db2ed8abd8f2782be079965e11a304e))

## [2.2.1-pre.6](https://github.com/pdreker/fritz_exporter/compare/v2.2.1-pre.5...v2.2.1-pre.6) (2022-12-29)



## [2.2.1-pre.5](https://github.com/pdreker/fritz_exporter/compare/v2.2.1-pre.4...v2.2.1-pre.5) (2022-12-29)



## [2.2.1-pre.4](https://github.com/pdreker/fritz_exporter/compare/v2.2.1-pre.3...v2.2.1-pre.4) (2022-12-29)



## [2.2.1-pre.3](https://github.com/pdreker/fritz_exporter/compare/v2.2.1-pre.2...v2.2.1-pre.3) (2022-12-29)


### Bug Fixes

* exporter does not honor log_level from config file. ([ad93945](https://github.com/pdreker/fritz_exporter/commit/ad93945eac60c780044946999d79735d0399d0f0)), closes [#116](https://github.com/pdreker/fritz_exporter/issues/116)
