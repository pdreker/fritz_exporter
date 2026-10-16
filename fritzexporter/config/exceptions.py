class ExporterError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class ConfigError(ExporterError):
    pass


class EmptyConfigError(ConfigError):
    pass


class ConfigFileUnreadableError(ConfigError):
    pass


class DeviceNamesNotUniqueError(ConfigError):
    pass


class NoDevicesFoundError(ConfigError):
    pass


class InvalidConfigValueError(ConfigError, ValueError):
    """A config value of the wrong type or out of range; the message names the key."""


class FritzPasswordMissingError(ConfigError):
    def __init__(self, *args: object) -> None:
        super().__init__(*(args or ("password or password_file is required",)))


class FritzPasswordTooLongError(ConfigError):
    def __init__(self, *args: object) -> None:
        super().__init__(
            *(
                args
                or (
                    (
                        "Password is longer than 32 characters! "
                        "Login may not succeed, please see documentation!"
                    ),
                )
            )
        )


class FritzPasswordFileDoesNotExistError(ConfigError):
    def __init__(self, *args: object) -> None:
        super().__init__(*(args or ("Password file does not exist!",)))


class FritzRemoteAccessRequiresTlsError(ConfigError):
    def __init__(self, *args: object) -> None:
        super().__init__(
            *(args or ("remote_access=true requires use_tls=true (AVM WAN TR-064 is HTTPS-only).",))
        )


# Copyright 2019-2026 Patrick Dreker <patrick@dreker.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
