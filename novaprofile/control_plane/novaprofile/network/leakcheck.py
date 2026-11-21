"""Post-launch leak verification.

After the browser has been launched with a profile, the control plane asks
this module to confirm that the running browser does not betray the host
machine.  Four independent checks are performed:

1. **WebRTC** -- the ICE candidates exposed to the page must not contain the
   machine's real public IP address.
2. **DNS** -- the effective ``--host-resolver-rules`` of the running browser
   must not let a hostname escape to the system resolver.
3. **Timezone** -- ``Intl.DateTimeFormat().resolvedOptions().timeZone`` must
   equal the timezone configured in the profile.
4. **Languages** -- ``navigator.languages`` must equal the languages
   configured in the profile.

The browser interaction is abstracted behind the :class:`CdpCall` protocol so
the whole module is unit-testable without a browser.  A thin concrete client
built on ``websockets`` is provided for production use.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from novaprofile.network.dns import is_leaking, parse_host_resolver_rules
from novaprofile.network.webrtc import (
    has_mdns_hostname,
    is_local_address,
    public_ip_mismatch,
    spoofed_local_ip,
)

__all__ = [
    "CdpCall",
    "CdpWebSocketClient",
    "LeakCheck",
    "LeakChecker",
    "LeakReport",
]


class CdpCall(Protocol):
    """A single CDP request/response round-trip.

    Implementations take a CDP method name plus a parameter mapping and
    return the decoded ``result`` object.
    """

    def __call__(
        self, method: str, params: dict[str, Any] | None = None
    ) -> Awaitable[dict[str, Any]]: ...


@dataclass(frozen=True)
class LeakReport:
    """Outcome of a leak verification run."""

    leaks: list[str] = field(default_factory=list)
    ok: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "leaks": list(self.leaks), "details": dict(self.details)}


@dataclass(frozen=True)
class LeakCheck:
    """The result of one individual check."""

    name: str
    ok: bool
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# JavaScript snippets evaluated inside the page
# --------------------------------------------------------------------------

#: Collects the ICE candidates a page can observe.
_COLLECT_ICE_JS: Final[str] = """
(async () => {
  const candidates = [];
  if (typeof RTCPeerConnection === 'undefined') {
    return { candidates, supported: false };
  }
  const pc = new RTCPeerConnection({ iceServers: [] });
  try {
    pc.createDataChannel('leakcheck');
    await pc.setLocalDescription(await pc.createOffer());
    await new Promise((resolve) => {
      if (pc.iceGatheringState === 'complete') { resolve(); return; }
      const timer = setTimeout(resolve, 3000);
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') { clearTimeout(timer); resolve(); }
      });
    });
    const stats = await pc.getStats();
    stats.forEach((report) => {
      if (report.type === 'local-candidate' && report.candidate) {
        candidates.push(report.candidate);
      }
    });
  } catch (err) {
    return { candidates, supported: true, error: String(err) };
  }
  return { candidates, supported: true };
})()
"""

#: Reads the page-visible timezone and languages.
_READ_IDENTITY_JS: Final[str] = """
(() => {
  let timeZone = null;
  try {
    timeZone = Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch (err) { timeZone = null; }
  return {
    timeZone: timeZone,
    languages: Array.from(navigator.languages || []),
    language: navigator.language || null,
    offsetMinutes: new Date().getTimezoneOffset()
  };
})()
"""


class LeakChecker:
    """Verify that a running browser does not leak the host identity."""

    def __init__(
        self,
        cfg: Any,
        *,
        cdp_call: CdpCall,
        real_public_ip: str | None = None,
        master_seed: str | None = None,
        expected_timezone: str | None = None,
        expected_languages: Sequence[str] | None = None,
        dns_test_host: str = "example.com",
    ) -> None:
        self._cfg = cfg
        self._cdp_call = cdp_call
        self._real_public_ip = real_public_ip
        self._master_seed = master_seed
        self._expected_timezone = expected_timezone
        self._expected_languages = list(expected_languages) if expected_languages else None
        self._dns_test_host = dns_test_host

    # -- configuration -----------------------------------------------------

    @property
    def expected_timezone(self) -> str | None:
        """The timezone the profile claims to be in."""
        if self._expected_timezone:
            return self._expected_timezone
        timezone = getattr(self._cfg, "timezone", None)
        name = getattr(timezone, "iana_name", None)
        return str(name) if name else None

    @property
    def expected_languages(self) -> list[str] | None:
        """The language list the profile claims to expose."""
        if self._expected_languages is not None:
            return list(self._expected_languages)
        navigator = getattr(self._cfg, "navigator", None)
        languages = getattr(navigator, "languages", None)
        if languages:
            return [str(item) for item in languages]
        locale = getattr(self._cfg, "locale", None)
        primary = getattr(locale, "primary", None)
        return [str(primary)] if primary else None

    @property
    def spoofed_local_ip(self) -> str | None:
        """The spoofed local address of the profile, if a seed is known."""
        if not self._master_seed:
            return None
        return spoofed_local_ip(self._master_seed)

    # -- checks ------------------------------------------------------------

    async def check(self) -> LeakReport:
        """Run every check and aggregate the results."""
        checks: list[LeakCheck] = []
        checks.append(await self.check_webrtc())
        checks.append(await self.check_dns())
        checks.append(await self.check_timezone())
        checks.append(await self.check_languages())

        leaks: list[str] = []
        details: dict[str, Any] = {}
        for check in checks:
            details[check.name] = {"ok": check.ok, "detail": check.detail, **check.data}
            if not check.ok:
                leaks.append(f"{check.name}: {check.detail}")
        return LeakReport(leaks=leaks, ok=not leaks, details=details)

    async def check_webrtc(self) -> LeakCheck:
        """Verify that the exposed ICE candidates hide the real public IP."""
        payload = await self._evaluate(_COLLECT_ICE_JS)
        candidates = _extract_candidates(payload)
        spoofed = self.spoofed_local_ip
        leaking: list[str] = []
        for candidate in candidates:
            if has_mdns_hostname(candidate):
                # An mDNS host candidate exposes the real machine name.
                leaking.append(candidate)
                continue
            address = _candidate_ip(candidate)
            if address is None:
                continue
            try:
                parsed = ipaddress.ip_address(address)
            except ValueError:
                continue
            if is_local_address(parsed):
                continue
            if self._real_public_ip and address == self._real_public_ip.strip():
                leaking.append(candidate)
                continue
            if spoofed and public_ip_mismatch(address, spoofed):
                leaking.append(candidate)
        if leaking:
            return LeakCheck(
                name="webrtc",
                ok=False,
                detail=f"real public IP exposed in {len(leaking)} ICE candidate(s)",
                data={"leaking_candidates": leaking, "candidates": candidates},
            )
        return LeakCheck(
            name="webrtc",
            ok=True,
            detail="no public ICE candidate exposes the real IP",
            data={"candidates": candidates},
        )

    async def check_dns(self) -> LeakCheck:
        """Verify that DNS is resolved through the proxy path."""
        rules = await self._host_resolver_rules()
        if rules is None:
            return LeakCheck(
                name="dns",
                ok=False,
                detail="could not read --host-resolver-rules from the browser",
                data={"resolver_rules": None},
            )
        leaking = is_leaking(rules, self._dns_test_host)
        details = {
            "resolver_rules": rules,
            "parsed_rules": [
                f"{kind} {' '.join(args)}" for kind, args in parse_host_resolver_rules(rules)
            ],
            "test_host": self._dns_test_host,
        }
        if leaking:
            return LeakCheck(
                name="dns",
                ok=False,
                detail=f"{self._dns_test_host} would resolve through the system resolver",
                data=details,
            )
        return LeakCheck(
            name="dns",
            ok=True,
            detail="DNS is resolved through the proxy path",
            data=details,
        )

    async def check_timezone(self) -> LeakCheck:
        """Verify that the page-visible timezone matches the profile."""
        expected = self.expected_timezone
        payload = await self._evaluate(_READ_IDENTITY_JS)
        actual = payload.get("timeZone")
        details = {"expected": expected, "actual": actual}
        if expected is None:
            return LeakCheck(
                name="timezone", ok=True, detail="no timezone configured", data=details
            )
        if actual != expected:
            return LeakCheck(
                name="timezone",
                ok=False,
                detail=f"timezone mismatch: expected {expected}, got {actual}",
                data=details,
            )
        return LeakCheck(name="timezone", ok=True, detail="timezone matches", data=details)

    async def check_languages(self) -> LeakCheck:
        """Verify that the page-visible languages match the profile."""
        expected = self.expected_languages
        payload = await self._evaluate(_READ_IDENTITY_JS)
        actual_raw = payload.get("languages")
        actual = [str(item) for item in actual_raw] if isinstance(actual_raw, list) else []
        details = {"expected": expected, "actual": actual}
        if not expected:
            return LeakCheck(
                name="languages", ok=True, detail="no languages configured", data=details
            )
        if actual != list(expected):
            return LeakCheck(
                name="languages",
                ok=False,
                detail=f"language mismatch: expected {expected}, got {actual}",
                data=details,
            )
        return LeakCheck(name="languages", ok=True, detail="languages match", data=details)

    # -- CDP helpers -------------------------------------------------------

    async def _evaluate(self, expression: str) -> dict[str, Any]:
        """Evaluate a JavaScript expression in the page and return its object."""
        result = await self._cdp_call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        )
        return _unwrap_evaluation(result)

    async def _host_resolver_rules(self) -> str | None:
        """Read the effective host-resolver rules from the browser command line."""
        try:
            result = await self._cdp_call("Browser.getBrowserCommandLine", {})
        except Exception:  # noqa: BLE001 - the method may be unavailable
            return None
        arguments = result.get("arguments") if isinstance(result, dict) else None
        if not isinstance(arguments, list):
            return None
        for argument in arguments:
            if not isinstance(argument, str):
                continue
            if argument.startswith("--host-resolver-rules="):
                return argument.partition("=")[2]
        return ""


# --------------------------------------------------------------------------
# Thin concrete CDP client
# --------------------------------------------------------------------------


class CdpWebSocketClient:
    """Minimal Chrome DevTools Protocol client built on ``websockets``.

    Only the subset needed by :class:`LeakChecker` is implemented: a single
    request/response round-trip over the browser's WebSocket endpoint.
    """

    def __init__(self, websocket_url: str, *, timeout: float = 20.0) -> None:
        self._url = websocket_url
        self._timeout = timeout
        self._message_id = 0
        self._connection: Any = None
        self._lock = asyncio.Lock()

    async def __call__(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Send a CDP command and await its result."""
        import websockets

        async with self._lock:
            if self._connection is None:
                self._connection = await websockets.connect(self._url)
            self._message_id += 1
            message_id = self._message_id
            payload = {"id": message_id, "method": method, "params": params or {}}
            await self._connection.send(json.dumps(payload))
            while True:
                raw = await asyncio.wait_for(self._connection.recv(), timeout=self._timeout)
                decoded = json.loads(raw)
                if decoded.get("id") != message_id:
                    continue
                if "error" in decoded:
                    raise RuntimeError(f"CDP error for {method}: {decoded['error']}")
                result = decoded.get("result", {})
                return result if isinstance(result, dict) else {}

    async def aclose(self) -> None:
        """Close the WebSocket connection."""
        if self._connection is not None:
            await self._connection.close()
            self._connection = None


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _unwrap_evaluation(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the object value from a ``Runtime.evaluate`` result."""
    if not isinstance(result, dict):
        return {}
    remote = result.get("result")
    if not isinstance(remote, dict):
        return {}
    value = remote.get("value")
    if isinstance(value, dict):
        return value
    return {}


def _extract_candidates(payload: dict[str, Any]) -> list[str]:
    """Pull the ICE candidate strings out of a page-evaluation payload."""
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, (str, int, float))]


def _candidate_ip(candidate: str) -> str | None:
    """Extract the IP address embedded in an ICE candidate string."""
    bracket = re.search(r"\[([0-9A-Fa-f:.]+)\]", candidate)
    if bracket:
        return bracket.group(1)
    dotted = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", candidate)
    if dotted:
        return dotted.group(1)
    return None
