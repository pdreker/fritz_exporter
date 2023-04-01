import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from typing import Optional

import requests
from attrs import define, field

from .exceptions import FritzHttpConnectionException, FritzHttpLoginException

logger = logging.getLogger("fritzexporter.http_connection")


@define
class LoginState:
    challenge: str = ""
    blocktime: int = 0
    is_pbkdf2: bool = field(init=False)

    def __attrs_post_init__(self):
        self.is_pbkdf2 = self.challenge.startswith("2$")


class FritzHttpConnection:
    LOGIN_SID_ROUTE: str = "/login_sid.lua?version=2"
    DATA_PAGE_ROUTE: str = "/data.lua"

    def __init__(self, host: str, username: str, password: str, port: int = 0, ssl: bool = False):
        self.host = host
        self.username = username
        self.password = password
        default_port: int = 80 if not ssl else 443
        self.port = port if port else default_port
        self.ssl = ssl
        self.sid = None
        self.login_state: Optional[LoginState] = None

        self.box_url = f"http{'s' if ssl else ''}://{self.host}:{self.port}"

        self.get_sid()
        logger.info(f"Successful HTTP login for user: {self.username} on {self.host}")
        logger.debug(f"HTTP sid on device {self.host}: {self.sid}")

    def get_sid(self):
        """Get a sid by solving the PBKDF2 (or MD5) challenge-response
        process."""
        self.get_login_state()
        if self.login_state is None:
            raise FritzHttpConnectionException("No login state found")

        if self.login_state.is_pbkdf2:
            logger.debug(f"Host: {self.host} PBKDF2 supported")
            challenge_response = self._calculate_pbkdf2_response()
        else:
            logger.debug(f"Host: {self.host} falling back to MD5")
            challenge_response = self._calculate_md5_response()

        if self.login_state.blocktime > 0:
            logger.warning(
                f"Host {self.host} has login blocktime: sleeping for "
                f"{self.login_state.blocktime} seconds..."
            )
            time.sleep(self.state.blocktime)

        try:
            sid = self.send_response(challenge_response)
        except Exception as ex:
            raise FritzHttpLoginException("failed to login") from ex
        if sid == "0000000000000000":
            raise FritzHttpLoginException("wrong username or password")
        self.sid = sid

    def get_login_state(self) -> None:
        """Get login state from FRITZ!Box using login_sid.lua?version=2"""
        url = self.box_url + self.LOGIN_SID_ROUTE
        logger.debug(f"Getting login state from {url}")
        try:
            http_response: requests.Response = requests.get(url)
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as ex:
            raise FritzHttpConnectionException(
                f"Failed to get challenge from host {self.host}"
            ) from ex

        if not http_response.ok:
            raise FritzHttpConnectionException(
                f"Failed to get challenge from host {self.host}, "
                f"received status code {http_response.status_code}"
            )

        xml = ET.fromstring(http_response.text)
        challenge = self._get_challenge_from_xml(xml)
        blocktime = self._get_blocktime_from_xml(xml)

        logger.debug(f"Host: {self.host} challenge: {challenge}")
        logger.debug(f"Host: {self.host} blocktime: {blocktime}")

        self.login_state = LoginState(challenge, blocktime)

    def _calculate_pbkdf2_response(self) -> str:
        """Calculate the response for a given challenge via PBKDF2"""
        if self.login_state is not None:
            challenge_parts = self.login_state.challenge.split("$")
            # Extract all necessary values encoded into the challenge
            iter1 = int(challenge_parts[1])
            salt1 = bytes.fromhex(challenge_parts[2])
            iter2 = int(challenge_parts[3])
            salt2 = bytes.fromhex(challenge_parts[4])
            # Hash twice, once with static salt...
            hash1 = hashlib.pbkdf2_hmac("sha256", self.password.encode(), salt1, iter1)
            # Once with dynamic salt.
            hash2 = hashlib.pbkdf2_hmac("sha256", hash1, salt2, iter2)
            return f"{challenge_parts[4]}${hash2.hex()}"
        else:
            raise FritzHttpConnectionException("no login state")

    def _calculate_md5_response(self) -> str:
        """Calculate the response for a challenge using legacy MD5"""
        if self.login_state is not None:
            response = self.login_state.challenge + "-" + self.password
            # the legacy response needs utf_16_le encoding
            legacy_response = response.encode("utf_16_le")
            md5_sum = hashlib.md5()
            md5_sum.update(legacy_response)
            response = self.login_state.challenge + "-" + md5_sum.hexdigest()
            return response
        else:
            raise FritzHttpConnectionException("no login state")

    def _get_challenge_from_xml(self, xml: ET.Element) -> str:
        """Extract the challenge from the XML response"""
        challenge = xml.find("Challenge")
        if challenge is not None and challenge.text is not None:
            return challenge.text
        else:
            raise FritzHttpConnectionException("no challenge in response")

    def _get_blocktime_from_xml(self, xml: ET.Element) -> int:
        """Extract the blocktime from the XML response"""
        blocktime = xml.find("BlockTime")
        if blocktime is not None and blocktime.text is not None:
            return int(blocktime.text)
        else:
            raise FritzHttpConnectionException("no blocktime in response")

    def _get_sid_from_xml(self, xml: ET.Element) -> str:
        """Extract the sid from the XML response"""
        sid = xml.find("SID")
        if sid is not None and sid.text is not None:
            return sid.text
        else:
            raise FritzHttpConnectionException("no sid in response")

    def send_response(self, challenge_response: str):
        """Send the response and return the parsed sid. raises an Exception on error"""
        # Build response params
        post_data_dict = {"username": self.username, "response": challenge_response}
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        url = self.box_url + FritzHttpConnection.LOGIN_SID_ROUTE

        # Send response
        try:
            http_response = requests.post(url, data=post_data_dict, headers=headers)
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as ex:
            raise FritzHttpConnectionException(
                f"Failed to send response to host {self.host}"
            ) from ex
        if not http_response.ok:
            raise FritzHttpConnectionException(
                f"Failed to send response to host {self.host}, "
                f"received status code {http_response.status_code}"
            )

        # Parse SID from resulting XML.
        xml = ET.fromstring(http_response.text)
        sid = self._get_sid_from_xml(xml)
        return sid

    def get_data_page(self, page: str) -> dict:
        """Get the data page from the FRITZ!Box"""
        url = self.box_url + self.DATA_PAGE_ROUTE
        logger.debug(f"Getting data page from {url}")
        try:
            http_response = requests.post(url, data={"sid": self.sid, "page": page})
        except (requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as ex:
            raise FritzHttpConnectionException(
                f"Failed to get data page {page} from host {self.host}"
            ) from ex
        if not http_response.ok:
            raise FritzHttpConnectionException(
                f"Failed to get data page {page} from host {self.host}, "
                f"received status code {http_response.status_code}"
            )

        return http_response.json()
