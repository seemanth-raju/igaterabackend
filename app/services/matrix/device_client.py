"""Matrix COSEC biometric device client.

API reference: http://<ip>/device.cgi/<endpoint>?action=<value>&...
Authentication: HTTP Digest Auth
"""

import hashlib
import logging
import os
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone

import requests
import urllib3
from requests.auth import HTTPDigestAuth

from app.core.security import decrypt_password
from app.utils import get_fingerprint_storage_path

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

_log = logging.getLogger(__name__)


class MatrixDeviceClient:
    """Client for interacting with Matrix COSEC biometric access devices."""

    def __init__(self, device_ip: str, username: str, encrypted_password: str, use_https: bool = False):
        self.device_ip = device_ip
        self.username = username
        self.password = decrypt_password(encrypted_password) if encrypted_password else ""
        self.protocol = "https" if use_https else "http"
        self.base_url = f"{self.protocol}://{self.device_ip}/device.cgi"
        self.auth = HTTPDigestAuth(self.username, self.password)
        self.timeout = (5, 30)

    @staticmethod
    def _is_success(response: requests.Response) -> bool:
        """Check if device response indicates success (Response-Code 0)."""
        return response.status_code == 200 and (
            "Response-Code=0" in response.text
            or "<Response-Code>0</Response-Code>" in response.text
        )

    # ------------------------------------------------------------------
    # Connectivity
    # ------------------------------------------------------------------

    def ping(self) -> bool:
        """Check device reachability. Returns True if online."""
        try:
            response = requests.get(
                f"{self.base_url}/device-basic-config",
                params={"action": "get"},
                auth=self.auth,
                timeout=(3, 5),
                verify=False,
            )
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def get_event_count(self) -> dict:
        """
        Get the current event sequence number and rollover count.
        Returns {"seq_number": int, "rollover_count": int} or {"error": str}.
        """
        try:
            response = requests.get(
                f"{self.base_url}/command",
                params={"action": "geteventcount", "format": "xml"},
                auth=self.auth,
                timeout=self.timeout,
                verify=False,
            )
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}
            root = ET.fromstring(response.text)
            seq = root.findtext("seq-number") or root.findtext("Seq-number") or "1"
            rollover = root.findtext("Roll-over-count") or root.findtext("roll-over-count") or "0"
            return {"seq_number": int(seq), "rollover_count": int(rollover)}
        except Exception as exc:
            return {"error": str(exc)}

    def fetch_events(self, rollover_count: int, seq_number: int, no_of_events: int = 100) -> list[dict]:
        """
        Fetch up to `no_of_events` events starting at seq_number.

        Returns list of dicts with keys:
            rollover_count, seq_number, event_time (datetime),
            cosec_event_id, detail_1..5
        """
        try:
            response = requests.get(
                f"{self.base_url}/events",
                params={
                    "action": "getevent",
                    "roll-over-count": rollover_count,
                    "seq-number": seq_number,
                    "no-of-events": no_of_events,
                    "format": "xml",
                },
                auth=self.auth,
                timeout=self.timeout,
                verify=False,
            )
            if response.status_code != 200:
                _log.warning("fetch_events: device %s returned HTTP %d", self.device_ip, response.status_code)
                return []

            _log.debug("fetch_events raw response from %s: %.500s", self.device_ip, response.text)

            root = ET.fromstring(response.text)
            event_nodes = root.findall("Events") or root.findall("Event")
            if not event_nodes:
                event_nodes = [c for c in root if "event" in c.tag.lower()]

            events = []
            for evt in event_nodes:
                raw_date = evt.findtext("date") or ""
                raw_time = evt.findtext("time") or "00:00:00"
                try:
                    day, month, year = raw_date.split("/")
                    event_time = datetime.strptime(
                        f"{year}-{month.zfill(2)}-{day.zfill(2)} {raw_time}", "%Y-%m-%d %H:%M:%S"
                    )
                except ValueError:
                    event_time = datetime.now(timezone.utc)

                events.append({
                    "rollover_count": int(evt.findtext("roll-over-count") or rollover_count),
                    "seq_number": int(evt.findtext("seq-No") or seq_number),
                    "event_time": event_time,
                    "cosec_event_id": int(evt.findtext("event-id") or 0),
                    "detail_1": evt.findtext("detail-1") or "",
                    "detail_2": evt.findtext("detail-2") or "",
                    "detail_3": evt.findtext("detail-3") or "",
                    "detail_4": evt.findtext("detail-4") or "",
                    "detail_5": evt.findtext("detail-5") or "",
                })
            return events
        except requests.exceptions.ConnectionError:
            _log.warning("fetch_events: cannot reach device %s", self.device_ip)
            return []
        except requests.exceptions.Timeout:
            _log.warning("fetch_events: device %s timed out", self.device_ip)
            return []
        except Exception:
            _log.exception("fetch_events: unexpected error on device %s", self.device_ip)
            return []

    # ------------------------------------------------------------------
    # User management
    # ------------------------------------------------------------------

    def get_user_count(self) -> int:
        """Get total enrolled user count. Returns -1 on error."""
        try:
            response = requests.get(
                f"{self.base_url}/command",
                params={"action": "getusercount", "format": "xml"},
                auth=self.auth,
                timeout=self.timeout,
                verify=False,
            )
            if response.status_code != 200:
                return -1
            body = response.text.strip()
            if "<" in body:
                root = ET.fromstring(body)
                count = root.findtext("user-count") or root.findtext("User-Count")
                return int(count) if count is not None else -1
            for part in body.split():
                if part.lower().startswith("user-count="):
                    return int(part.split("=")[1])
            return -1
        except Exception:
            _log.exception("get_user_count: error from device %s", self.device_ip)
            return -1

    def create_user(
        self,
        user_id: str,
        name: str,
        active: bool = True,
        validity_end_date: date | None = None,
        enable_fr: str | None = None,
        card1: str | None = None,
    ) -> dict:
        """
        Create or update a user on the device.

        Args:
            user_id: Unique user identifier (max 15 chars)
            name: User display name (max 15 chars)
            active: Whether user can access the door
            validity_end_date: Optional date after which device denies access
            enable_fr: "1" to enable face recognition, "0" to disable
            card1: Optional RFID card number
        """
        params: dict = {
            "action": "set",
            "user-id": str(user_id),
            "ref-user-id": str(user_id),
            "name": name,
            "user-active": "1" if active else "0",
            "format": "xml",
        }
        if validity_end_date is not None:
            params["validity-enable"] = "1"
            params["validity-date-dd"] = str(validity_end_date.day)
            params["validity-date-mm"] = str(validity_end_date.month)
            params["validity-date-yyyy"] = str(validity_end_date.year)
        if enable_fr:
            params["enable-fr"] = str(enable_fr)
        if card1:
            params["card1"] = str(card1)

        response = requests.get(
            f"{self.base_url}/users",
            params=params,
            auth=self.auth,
            timeout=self.timeout,
            verify=False,
        )
        success = self._is_success(response)
        return {"status_code": response.status_code, "response": response.text, "success": success}

    def delete_user(self, user_id: str) -> dict:
        """Delete a user from the device."""
        response = requests.get(
            f"{self.base_url}/users",
            params={"action": "delete", "user-id": str(user_id), "format": "xml"},
            auth=self.auth,
            timeout=self.timeout,
            verify=False,
        )
        success = self._is_success(response)
        return {"status_code": response.status_code, "response": response.text, "success": success}

    def list_users(self) -> list[str]:
        """
        Return all user-id strings currently enrolled on the device.
        Iterates by user-index until all users are found or a gap threshold is hit.
        Returns [] on error.
        """
        total = self.get_user_count()
        if total <= 0:
            return []

        user_ids: list[str] = []
        max_index = min(total * 3, 5_000)
        consecutive_empty = 0

        for idx in range(1, max_index + 1):
            if len(user_ids) >= total:
                break
            try:
                response = requests.get(
                    f"{self.base_url}/users",
                    params={"action": "get", "user-index": idx, "format": "xml"},
                    auth=self.auth,
                    timeout=(3, 10),
                    verify=False,
                )
                body = response.text.strip()

                if response.status_code != 200 or not body:
                    consecutive_empty += 1
                    if consecutive_empty > 30:
                        break
                    continue

                if "Response-Code=10" in body or "<Response-Code>10</Response-Code>" in body:
                    consecutive_empty += 1
                    continue

                consecutive_empty = 0
                uid: str | None = None
                if "<" in body:
                    try:
                        root = ET.fromstring(body)
                        rc = root.findtext("Response-Code") or root.findtext("response-code")
                        if rc and rc.strip() not in ("0", ""):
                            continue
                        uid = root.findtext("user-id") or root.findtext("User-Id")
                    except ET.ParseError:
                        pass
                if uid is None:
                    for part in body.split():
                        if part.lower().startswith("user-id="):
                            uid = part.split("=", 1)[1]
                            break
                if uid and uid.strip() and uid.strip() != "0":
                    user_ids.append(uid.strip())

            except requests.exceptions.ConnectionError:
                _log.warning("list_users: cannot reach device %s at index %d", self.device_ip, idx)
                return []
            except requests.exceptions.Timeout:
                _log.warning("list_users: timeout on device %s at index %d", self.device_ip, idx)
                return []
            except Exception:
                _log.exception("list_users: error at user-index %d on device %s", idx, self.device_ip)
                continue

        return user_ids

    def wipe_all_users(self, max_index: int = 2000, stop_after_misses: int = 50) -> dict:
        """
        Delete every user on the device by iterating user-index slots.
        Stops after `stop_after_misses` consecutive empty slots.
        Returns {"deleted": [uid, ...], "errors": [{user_id, error}, ...]}.
        """
        deleted: list[str] = []
        errors: list[dict] = []
        consecutive_misses = 0

        for idx in range(1, max_index + 1):
            if consecutive_misses >= stop_after_misses:
                break
            try:
                response = requests.get(
                    f"{self.base_url}/users",
                    params={"action": "get", "user-index": idx, "format": "xml"},
                    auth=self.auth,
                    timeout=(3, 10),
                    verify=False,
                )
                body = response.text.strip()

                if (
                    response.status_code != 200
                    or not body
                    or "Response-Code=10" in body
                    or "<Response-Code>10</Response-Code>" in body
                ):
                    consecutive_misses += 1
                    continue

                consecutive_misses = 0
                uid: str | None = None
                if "<" in body:
                    try:
                        root = ET.fromstring(body)
                        uid = root.findtext("user-id") or root.findtext("User-Id")
                    except ET.ParseError:
                        pass
                if uid is None:
                    for part in body.split():
                        if part.lower().startswith("user-id="):
                            uid = part.split("=", 1)[1]
                            break

                if not uid or uid.strip() == "0":
                    continue
                uid = uid.strip()

                self.delete_fingerprint(uid)
                result = self.delete_user(uid)
                if result["success"]:
                    deleted.append(uid)
                else:
                    errors.append({"user_id": uid, "error": result["response"]})

            except requests.exceptions.ConnectionError:
                _log.warning("wipe_all_users: lost connection to %s at index %d", self.device_ip, idx)
                break
            except Exception:
                _log.exception("wipe_all_users: error at index %d on %s", idx, self.device_ip)
                continue

        _log.info("wipe_all_users: %s — deleted %d, errors %d", self.device_ip, len(deleted), len(errors))
        return {"deleted": deleted, "errors": errors}

    # ------------------------------------------------------------------
    # Fingerprint credentials (cross-device sync)
    # ------------------------------------------------------------------

    def trigger_fingerprint_enrollment(self, user_id: str, finger_index: int = 1) -> dict:
        """
        Put the device into fingerprint enrollment mode for this user.
        The user must then place their finger on the device sensor.

        After calling this, wait for the user to scan their finger, then
        call extract_fingerprint() to retrieve the captured template.
        """
        response = requests.get(
            f"{self.base_url}/enrolluser",
            params={
                "action": "enroll",
                "type": "2",        # 2 = Fingerprint
                "user-id": str(user_id),
                "format": "xml",
            },
            auth=self.auth,
            timeout=self.timeout,
            verify=False,
        )
        success = self._is_success(response)
        return {"status_code": response.status_code, "response": response.text, "success": success}

    def extract_fingerprint(self, user_id: str, finger_index: int = 1) -> tuple[bytes | None, str | None]:
        """
        Pull a fingerprint template from the device and save it to local storage.

        Returns (template_bytes, file_path) or (None, None) if not yet captured.
        Call this after trigger_fingerprint_enrollment() and user has placed finger.
        """
        response = requests.post(
            f"{self.base_url}/credential",
            params={"action": "get", "type": "1", "user-id": str(user_id), "finger-index": str(finger_index)},
            auth=self.auth,
            timeout=self.timeout,
            verify=False,
        )
        if response.status_code != 200 or not response.content:
            return None, None

        content = response.content
        # Reject tiny error-message responses
        if len(content) < 100 or b"Response-Code=" in content:
            return None, None

        storage_path = get_fingerprint_storage_path()
        file_name = f"tenant_{user_id}_finger_{finger_index}.dat"
        file_path = storage_path / file_name
        file_path.write_bytes(content)
        return content, str(file_path)

    def import_fingerprint(self, user_id: str, file_path: str, finger_index: int = 1) -> dict:
        """
        Push a stored fingerprint template to the device.

        Use this to enroll a tenant on additional devices without requiring
        them to place their finger again.

        Args:
            user_id: Target user on the device
            file_path: Path to the .dat fingerprint template file
            finger_index: Finger slot index (1-10)
        """
        if not os.path.exists(file_path):
            return {"status_code": 0, "response": f"File not found: {file_path}", "success": False}

        with open(file_path, "rb") as f:
            binary_data = f.read()

        response = requests.post(
            f"{self.base_url}/credential",
            params={"action": "set", "type": "1", "user-id": str(user_id), "finger-index": str(finger_index)},
            data=binary_data,
            headers={"Content-Type": "application/octet-stream"},
            auth=self.auth,
            timeout=self.timeout,
            verify=False,
        )
        success = self._is_success(response)
        return {"status_code": response.status_code, "response": response.text, "success": success}

    def delete_fingerprint(self, user_id: str) -> dict:
        """Delete all fingerprint templates for a user on the device."""
        response = requests.get(
            f"{self.base_url}/credential",
            params={"action": "delete", "user-id": str(user_id), "type": "1", "format": "xml"},
            auth=self.auth,
            timeout=self.timeout,
            verify=False,
        )
        success = self._is_success(response)
        return {"status_code": response.status_code, "response": response.text, "success": success}


def calculate_file_hash(file_path: str) -> str:
    """Return SHA256 hex digest of a file, or '' if not found."""
    if not os.path.exists(file_path):
        return ""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256.update(chunk)
    return sha256.hexdigest()
