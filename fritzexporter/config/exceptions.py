class ExporterError(Exception):
    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class ConfigError(ExporterError):
    pass


class EmptyConfigError(ExporterError):
    pass


class ConfigFileUnreadableError(ExporterError):
    pass


class DeviceNamesNotUniqueError(ExporterError):
    pass


class NoDevicesFoundError(ExporterError):
    pass


class FritzPasswordTooLongError(ExporterError):
    def __init__(self) -> None:
        super().__init__(
            "Password is longer than 32 characters! "
            "Login may not succeed, please see documentation!"
        )


class FritzPasswordFileDoesNotExistError(ExporterError):
    def __init__(self) -> None:
        super().__init__("Password file does not exist!")


# Copyright 2019-2024 Patrick Dreker <patrick@dreker.de>
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
