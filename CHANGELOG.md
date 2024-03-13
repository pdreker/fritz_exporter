## [2.2.1](https://github.com/pdreker/fritz_exporter/compare/v2.2.1-pre.6...v2.2.1) (2022-12-29)



## [2.5.0](https://github.com/pdreker/fritz_exporter/compare/fritzexporter-v2.4.3...fritzexporter-v2.5.0) (2024-03-13)


### Features

* make listen address configurable (by @NyCodeGHG in [#315](https://github.com/pdreker/fritz_exporter/issues/315)) ([#316](https://github.com/pdreker/fritz_exporter/issues/316)) ([abc1671](https://github.com/pdreker/fritz_exporter/commit/abc1671dd74d6d4e480ec5747e12b08b1ffd0609))

## [2.4.3](https://github.com/pdreker/fritz_exporter/compare/fritzexporter-v2.4.2...fritzexporter-v2.4.3) (2024-03-10)


### Bug Fixes

* minor linting, make ARM builds work again ([#308](https://github.com/pdreker/fritz_exporter/issues/308)) ([1632029](https://github.com/pdreker/fritz_exporter/commit/16320292dc1ea5c1fba1f5d6dc4a5bb05467f579))

## [2.4.2](https://github.com/pdreker/fritz_exporter/compare/fritzexporter-v2.4.1...fritzexporter-v2.4.2) (2024-03-10)


### Documentation

* update copyright notice years ([0d19d27](https://github.com/pdreker/fritz_exporter/commit/0d19d27e4fc868d234c30c368f1aa8cb350866fd))
* Update Docker build instruction. ([65064f4](https://github.com/pdreker/fritz_exporter/commit/65064f47c446a88a1470f082393452b982af4234))

## [2.4.1](https://github.com/pdreker/fritz_exporter/compare/fritzexporter-v2.4.0...fritzexporter-v2.4.1) (2024-03-10)


### Bug Fixes

* ignore exceptions from parsing XML of AHA devices ([#303](https://github.com/pdreker/fritz_exporter/issues/303)) ([02197fa](https://github.com/pdreker/fritz_exporter/commit/02197fab4bb74eff8488ae03b84008535735c883))

## [2.4.0](https://github.com/pdreker/fritz_exporter/compare/fritzexporter-v2.3.1...fritzexporter-v2.4.0) (2024-03-09)


### Features

* add Homeautomation metrics via HTTP ([#273](https://github.com/pdreker/fritz_exporter/issues/273)) ([72f1361](https://github.com/pdreker/fritz_exporter/commit/72f136160943f4e9f3a9feec7c4d156af2b5e4cd))
* allow reading password from a file ([#296](https://github.com/pdreker/fritz_exporter/issues/296)) ([369f007](https://github.com/pdreker/fritz_exporter/commit/369f007f0543dffb6170bd16557a06c554d824bd))


### Bug Fixes

* Add more flexibility to helper script. ([#280](https://github.com/pdreker/fritz_exporter/issues/280)) ([86697fa](https://github.com/pdreker/fritz_exporter/commit/86697fa075c980530c9c45c6822cab9d44579a2e))
* **helm:** ServiceMonitor seems to like quotes now ([2e57ee9](https://github.com/pdreker/fritz_exporter/commit/2e57ee98d035cb27add47790852f29415d97b008))
* small correction for AHA HTTP metrics ([6658b8a](https://github.com/pdreker/fritz_exporter/commit/6658b8ad55374e00741a6dbc15ae709d99d43c30))

## [2.3.1](https://github.com/pdreker/fritz_exporter/compare/fritzexporter-v2.3.0...fritzexporter-v2.3.1) (2023-12-16)


### Bug Fixes

* add type hints to data_donation.py ([369748a](https://github.com/pdreker/fritz_exporter/commit/369748a2fb1672b01bf1d9b44845b0368d989c28))


### Documentation

* **homeauto:** explicitly mention missing metrics ([d827b5d](https://github.com/pdreker/fritz_exporter/commit/d827b5daa4d8f61fcbcf99c9df08ba6005992e77))

## [2.3.0](https://github.com/pdreker/fritz_exporter/compare/v2.2.4...v2.3.0) (2023-10-03)


### Features

* add home automation metrics ([5f35afb](https://github.com/pdreker/fritz_exporter/commit/5f35afb335ccc84333bb9219185e7409e051f47a))


### Bug Fixes

* add .readthedocs.yaml config file ([df7b150](https://github.com/pdreker/fritz_exporter/commit/df7b150f00f0542559a745fc02c4b40b4557c34a))
* make bail out message less verbose ([724fd01](https://github.com/pdreker/fritz_exporter/commit/724fd011a55485b7ddab3f69061fbdb2eb34326f))


### Documentation

* update copyright boilerplate ([baafe01](https://github.com/pdreker/fritz_exporter/commit/baafe01d6a8fffbe241db03b6fa813f4248ddb8d))

## [2.2.4](https://github.com/pdreker/fritz_exporter/compare/v2.2.3...v2.2.4) (2023-08-12)


### Bug Fixes

* returning the same metric samples multiple times ([020a7b7](https://github.com/pdreker/fritz_exporter/commit/020a7b78e893bd03778f67e43e37b94cc02aad92))
* When checking for capabilities fritzconnection may return FritzArgumentError which needs to be caught. ([87a47ee](https://github.com/pdreker/fritz_exporter/commit/87a47ee5fcf19e9cdb186f89c8230c443b07ccce))

## [2.2.3](https://github.com/pdreker/fritz_exporter/compare/v2.2.2...v2.2.3) (2023-04-01)


### Bug Fixes

* rmeove old actions and supersede release 2.2.2 ([1be640b](https://github.com/pdreker/fritz_exporter/commit/1be640b3a692a1402c1c4f85a3db6297b034fe01))

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
