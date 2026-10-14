"""WebDriverAgent (WDA) HTTP client for physical iPhone control."""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, Optional, Tuple

import requests


class WDAError(RuntimeError):
    """Raised when a WDA request fails."""


class WDAClient:
    """
    Minimal client for Facebook/Appium WebDriverAgent.

    Expects WDA reachable at ``base_url`` (default http://127.0.0.1:8100),
    typically after:
      - building/running WebDriverAgentRunner on the device via Xcode
      - ``pymobiledevice3 usbmux forward 8100 8100`` (and tunnel on iOS 17+)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8100",
        bundle_id: Optional[str] = None,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.bundle_id = bundle_id
        self.timeout = timeout
        self.session_id: Optional[str] = None
        self._http = requests.Session()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url}{path}"

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, Any]] = None,
        *,
        ok_statuses: Tuple[int, ...] = (200,),
    ) -> Dict[str, Any]:
        try:
            resp = self._http.request(
                method,
                self._url(path),
                json=json_body,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise WDAError(f"WDA connection failed ({self.base_url}): {exc}") from exc

        if resp.status_code not in ok_statuses:
            raise WDAError(
                f"WDA {method} {path} -> HTTP {resp.status_code}: {resp.text[:500]}"
            )

        if not resp.content:
            return {}

        try:
            payload = resp.json()
        except ValueError as exc:
            raise WDAError(f"WDA returned non-JSON: {resp.text[:200]}") from exc

        # WDA wraps results as {"value": ..., "sessionId": ...}
        if isinstance(payload, dict) and payload.get("status") not in (None, 0):
            raise WDAError(f"WDA error status={payload.get('status')}: {payload}")
        return payload if isinstance(payload, dict) else {"value": payload}

    def status(self) -> Dict[str, Any]:
        """GET /status — cheap health check."""
        return self._request("GET", "/status")

    def ensure_ready(self) -> None:
        """Verify WDA is up and create a session if needed."""
        info = self.status()
        logging.info("WDA status OK: %s", info.get("value", info))
        if not self.session_id:
            self.create_session()

    def create_session(self) -> str:
        """Start a WDA session (SpringBoard / optional bundle)."""
        caps: Dict[str, Any] = {}
        if self.bundle_id:
            caps["bundleId"] = self.bundle_id

        # Raw WDA accepts either nested capabilities or a flat dict.
        bodies = [
            {"capabilities": {"alwaysMatch": caps}} if caps else {"capabilities": {}},
            {"desiredCapabilities": caps} if caps else {"desiredCapabilities": {}},
            {"capabilities": caps} if caps else {"capabilities": {}},
        ]

        last_error: Optional[Exception] = None
        for body in bodies:
            try:
                payload = self._request("POST", "/session", body, ok_statuses=(200, 201))
                session_id = (
                    payload.get("sessionId")
                    or (payload.get("value") or {}).get("sessionId")
                    if isinstance(payload.get("value"), dict)
                    else None
                )
                if not session_id and isinstance(payload.get("value"), str):
                    session_id = payload["value"]
                if session_id:
                    self.session_id = session_id
                    logging.info("WDA session created: %s", session_id)
                    return session_id
            except WDAError as exc:
                last_error = exc
                continue

        raise WDAError(f"Could not create WDA session: {last_error}")

    def delete_session(self) -> None:
        if not self.session_id:
            return
        try:
            self._request("DELETE", f"/session/{self.session_id}", ok_statuses=(200, 404))
        except WDAError as exc:
            logging.warning("Failed to delete WDA session: %s", exc)
        finally:
            self.session_id = None

    def _require_session(self) -> str:
        if not self.session_id:
            self.create_session()
        assert self.session_id
        return self.session_id

    def window_size(self) -> Tuple[int, int]:
        """Return (width, height) in points/pixels as reported by WDA."""
        sid = self._require_session()
        payload = self._request("GET", f"/session/{sid}/window/size")
        value = payload.get("value") or {}
        width = int(value.get("width") or 0)
        height = int(value.get("height") or 0)
        if width <= 0 or height <= 0:
            raise WDAError(f"Invalid window size from WDA: {payload}")
        return width, height

    def screenshot_png(self) -> bytes:
        """Capture current screen as PNG bytes."""
        sid = self._require_session()
        # Prefer session screenshot; fall back to global /screenshot.
        try:
            payload = self._request("GET", f"/session/{sid}/screenshot")
        except WDAError:
            payload = self._request("GET", "/screenshot")

        value = payload.get("value")
        if not isinstance(value, str) or not value:
            raise WDAError("WDA screenshot missing base64 value")
        return base64.b64decode(value)

    def tap(self, x: int, y: int) -> None:
        """Tap at integer screen coordinates."""
        sid = self._require_session()
        body = {"x": int(x), "y": int(y)}
        # Appium/Facebook WDA endpoint
        try:
            self._request("POST", f"/session/{sid}/wda/tap", body)
            return
        except WDAError:
            pass
        # Some builds want element uuid omitted / alternate path
        try:
            self._request("POST", f"/session/{sid}/wda/tap/0", body)
            return
        except WDAError:
            pass
        # W3C pointer actions fallback
        actions = {
            "actions": [
                {
                    "type": "pointer",
                    "id": "finger1",
                    "parameters": {"pointerType": "touch"},
                    "actions": [
                        {"type": "pointerMove", "duration": 0, "x": int(x), "y": int(y)},
                        {"type": "pointerDown", "button": 0},
                        {"type": "pause", "duration": 50},
                        {"type": "pointerUp", "button": 0},
                    ],
                }
            ]
        }
        self._request("POST", f"/session/{sid}/actions", actions)

    def swipe(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        duration: float = 0.3,
    ) -> None:
        """Drag/swipe between two points."""
        sid = self._require_session()
        body = {
            "fromX": int(from_x),
            "fromY": int(from_y),
            "toX": int(to_x),
            "toY": int(to_y),
            "duration": float(duration),
        }
        try:
            self._request("POST", f"/session/{sid}/wda/dragfromtoforduration", body)
            return
        except WDAError:
            # Some servers use camelCase path segment
            self._request("POST", f"/session/{sid}/wda/dragFromToForDuration", body)

    def type_text(self, text: str) -> None:
        """Type into the currently focused field."""
        sid = self._require_session()
        # Prefer keys API (handles unicode better than raw sendKeys on many builds)
        try:
            self._request(
                "POST",
                f"/session/{sid}/wda/keys",
                {"value": list(text)},
            )
            return
        except WDAError:
            pass
        self._request(
            "POST",
            f"/session/{sid}/wda/keys",
            {"value": [text]},
        )

    def home(self) -> None:
        """Press the home button / go to SpringBoard."""
        try:
            self._request("POST", "/wda/homescreen")
        except WDAError:
            sid = self._require_session()
            self._request("POST", f"/session/{sid}/wda/homescreen")

    def open_url(self, url: str) -> None:
        """Open a URL (deep link / Safari)."""
        sid = self._require_session()
        self._request("POST", f"/session/{sid}/url", {"url": url})
