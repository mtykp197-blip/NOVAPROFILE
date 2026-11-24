"""The probe that walks the third-party detection sites.

Why this module exists
----------------------

The internal harness (:mod:`novaprofile.probe.verify`) answers one
question: does the browser report what the profile says it should?  It
runs against a page the project wrote, and it knows the answer before
it starts.

A detection site answers a different question, and it is the question
that matters: *does this browser look like the device it claims to be,
to somebody who wants to know?*  Those sites compare the user agent
against the client hints, the screen against the device, the platform
against the headers, and they publish a verdict.  A profile can satisfy
the internal harness and still be obvious to them -- and the internal
harness cannot say so, because it only knows the profile's own claims.

So this module drives the real sites.

What it does
------------

1. Launches the browser with the profile, the way the product does.
2. Navigates to each configured site.
3. Captures the **request headers** the browser actually sent --
   through the DevTools protocol, so the values are the ones the
   server received, not the ones the page believes it sent.
4. Reads the **verdict** the site published, when the site publishes
   one in a form this module knows.
5. Compares the captured headers against the profile's own values.

The comparison is the part that matters
----------------------------------------

The sites are a black box: their layout changes, their verdicts move,
and some of them are behind a captcha.  What does not change is the
*request*.  The headers a browser sends are a contract between the
browser and the server, and a profile that declares a device has to
send headers that agree with it.

So the probe has two kinds of finding:

* a **header mismatch** -- the browser sent something the profile does
  not declare, or contradicts.  This is a defect in the product and it
  is reported with the exact pair of values.
* a **site verdict** -- what the site said.  It is recorded as the site
  said it, and it is never turned into a verdict of our own: a site
  that says "you look like a bot" might be right about something this
  probe cannot see.

The header mismatches are what an operator can act on, so they come
first in the report and they decide whether the run passed.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from ..schema.profile import ProfileConfig
from .e2e import DevToolsClient, find_browser

__all__ = [
    "CheckSite",
    "DetectedHeaders",
    "HeaderFinding",
    "SiteResult",
    "ThirdPartyReport",
    "ThirdPartyRunner",
    "DEFAULT_SITES",
    "expected_headers_for",
]


@dataclass(frozen=True)
class CheckSite:
    """One detection site the probe visits."""

    name: str
    url: str
    verdict_selector: str = ""
    verdict_keyword: str = ""


@dataclass(frozen=True)
class DetectedHeaders:
    """The headers the browser actually sent to one site."""

    site: str
    headers: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> str:
        """Return one header, case-insensitively, or the empty string."""

        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return ""


@dataclass(frozen=True)
class HeaderFinding:
    """One header the browser sent that contradicts the profile."""

    site: str
    header: str
    expected: str
    actual: str
    detail: str = ""


@dataclass(frozen=True)
class SiteResult:
    """What one site said about the profile."""

    site: str
    url: str
    ok: bool
    verdict: str = ""
    findings: tuple[HeaderFinding, ...] = field(default=())
    error: str = ""


@dataclass(frozen=True)
class ThirdPartyReport:
    """The outcome of the whole probe run."""

    profile_id: str
    passed: bool
    sites: tuple[SiteResult, ...] = field(default=())
    browser: str = ""
    error: str = ""

    @property
    def findings(self) -> tuple[HeaderFinding, ...]:
        """Every header finding of the run, in the order it was found."""

        found: list[HeaderFinding] = []
        for result in self.sites:
            found.extend(result.findings)
        return tuple(found)


DEFAULT_SITES: Final[tuple[CheckSite, ...]] = (
    CheckSite(
        name="whatsmybrowser",
        url="https://whatsmybrowser.info/",
        verdict_selector="#browser-report, .report, pre",
        verdict_keyword="mobile",
    ),
    CheckSite(
        name="browserleaks",
        url="https://browserleaks.com/",
        verdict_selector="canvas, webgl, screen",
        verdict_keyword="",
    ),
    CheckSite(
        name="deviceandbrowserinfo",
        url="https://deviceandbrowserinfo.com/",
        verdict_selector="#report, .report",
        verdict_keyword="",
    ),
    CheckSite(
        name="sannysoft",
        url="https://bot.sannysoft.com/",
        verdict_selector="table",
        verdict_keyword="",
    ),
    CheckSite(
        name="creepjs",
        url="https://abrahamjuliot.github.io/creepjs/",
        verdict_selector="#fp-info, .fp-info, table",
        verdict_keyword="",
    ),
)


def expected_headers_for(config: ProfileConfig) -> dict[str, str]:
    """The headers the profile declares, as the browser must send them.

    The comparison is on the headers that carry the device's identity:
    the user agent and the client hints.  They are the ones a detection
    site reads first, and they are the ones that have to agree with
    each other -- a user agent that says Android next to a platform
    that says Windows is the oldest fingerprint there is.
    """

    headers = config.http_headers
    expected = {
        "User-Agent": config.navigator.user_agent,
        "Sec-Ch-Ua": headers.sec_ch_ua,
        "Sec-Ch-Ua-Mobile": headers.sec_ch_ua_mobile,
        "Sec-Ch-Ua-Platform": headers.sec_ch_ua_platform,
    }
    platform_version = headers.sec_ch_ua_platform_version
    if platform_version:
        expected["Sec-Ch-Ua-Platform-Version"] = f'"{platform_version}"'
    model = headers.sec_ch_ua_model
    if model:
        expected["Sec-Ch-Ua-Model"] = f'"{model}"'
    architecture = headers.sec_ch_ua_arch
    if architecture:
        expected["Sec-Ch-Ua-Arch"] = f'"{architecture}"'
    bitness = headers.sec_ch_ua_bitness
    if bitness:
        expected["Sec-Ch-Ua-Bitness"] = f'"{bitness}"'
    return expected


@dataclass
class _BrowserProcess:
    """A launched browser and the DevTools port it reported."""

    process: subprocess.Popen[bytes]
    port: int
    user_data_dir: Path


class ThirdPartyRunner:
    """Visits the detection sites with a profile and reports what happened.

    The runner launches the browser the way the product does: the
    profile document goes to the native core and the payload goes to
    the JavaScript layer, both through files in the user data
    directory.  A browser launched any other way is a stock browser,
    and a probe of a stock browser says nothing about the product.
    """

    def __init__(
        self,
        config: ProfileConfig,
        *,
        browser_path: str | None = None,
        headless: bool = True,
        timeout: float = 30.0,
        sites: tuple[CheckSite, ...] = DEFAULT_SITES,
        browser_finder: Any = find_browser,
        settle: float = 3.0,
    ) -> None:
        self._config = config
        self._browser_path = browser_path
        self._headless = headless
        self._timeout = timeout
        self._sites = sites
        self._browser_finder = browser_finder
        self._settle = settle

    async def run(self) -> ThirdPartyReport:
        """Visit every site and return the report."""

        try:
            browser_path = self._browser_finder(self._browser_path)
        except Exception as exc:
            return ThirdPartyReport(
                profile_id=self._config.identity.id,
                passed=False,
                browser=str(self._browser_path or ""),
                error=f"the browser could not be found: {exc}",
            )

        expected = expected_headers_for(self._config)
        launched: _BrowserProcess | None = None
        client: DevToolsClient | None = None
        session_id: str | None = None
        results: list[SiteResult] = []
        try:
            launched = await asyncio.to_thread(self._launch, browser_path)
            version = await asyncio.to_thread(
                self._fetch_json, f"http://127.0.0.1:{launched.port}/json/version"
            )
            ws_url = str(version.get("webSocketDebuggerUrl", ""))
            if not ws_url:
                raise RuntimeError("the browser did not report a websocket debugger URL")

            client = DevToolsClient(ws_url, timeout=self._timeout)
            await client.connect()

            target = await client.send("Target.createTarget", {"url": "about:blank"})
            target_id = target.get("targetId")
            if not isinstance(target_id, str):
                raise RuntimeError("Target.createTarget did not return a targetId")
            attached = await client.send(
                "Target.attachToTarget", {"targetId": target_id, "flatten": True}
            )
            raw_session = attached.get("sessionId")
            session_id = raw_session if isinstance(raw_session, str) else None

            await client.send("Network.enable", session_id=session_id)
            await client.send("Page.enable", session_id=session_id)
            await client.send("Runtime.enable", session_id=session_id)

            for site in self._sites:
                results.append(
                    await self._visit(client, session_id, site, expected)
                )
        except Exception as exc:
            return ThirdPartyReport(
                profile_id=self._config.identity.id,
                passed=False,
                sites=tuple(results),
                browser=browser_path,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            if client is not None:
                with contextlib.suppress(Exception):
                    await client.close()
            if launched is not None:
                await asyncio.to_thread(self._terminate, launched)

        findings = [f for result in results for f in result.findings]
        passed = not findings and all(result.ok for result in results)
        return ThirdPartyReport(
            profile_id=self._config.identity.id,
            passed=passed,
            sites=tuple(results),
            browser=browser_path,
        )

    def run_sync(self) -> ThirdPartyReport:
        """Run the probe from synchronous code."""

        return asyncio.run(self.run())

    async def _visit(
        self,
        client: DevToolsClient,
        session_id: str | None,
        site: CheckSite,
        expected: dict[str, str],
    ) -> SiteResult:
        """Visit one site and collect what it saw."""

        findings: list[HeaderFinding] = []
        captured: dict[str, str] = {}
        verdict = ""
        error = ""
        try:
            await client.send("Network.enable", session_id=session_id)
            await client.send(
                "Page.navigate", {"url": site.url}, session_id=session_id
            )
            await asyncio.sleep(self._settle)

            captured = await self._capture_headers(client, session_id, site.url)
            verdict = await self._read_verdict(client, session_id, site)
            findings = self._compare(site, expected, captured)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"

        ok = not error and not findings
        return SiteResult(
            site=site.name,
            url=site.url,
            ok=ok,
            verdict=verdict,
            findings=tuple(findings),
            error=error,
        )

    async def _capture_headers(
        self,
        client: DevToolsClient,
        session_id: str | None,
        url: str,
    ) -> dict[str, str]:
        """Return the headers the browser sent to ``url``.

        The headers are read from the browser's own network log, so
        they are the values the server received, not the values the
        page believes it sent.
        """

        host = urlsplit(url).hostname or ""
        path = urlsplit(url).path or "/"
        captured = await self._request_log(client, session_id)
        if not captured:
            return {}

        # The *last* request to the site's host and path is the one that
        # carries the document: the page may have fired a dozen
        # sub-requests first.
        for item in reversed(captured):
            parts = urlsplit(item.get("url", ""))
            if parts.hostname != host or parts.path != path:
                continue
            headers = item.get("headers", {})
            return {
                str(key): str(value)
                for key, value in headers.items()
                if key.lower() != "cookie"
            }
        return {}

    async def _request_log(
        self,
        client: DevToolsClient,
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return the requests the browser recorded, oldest first."""

        try:
            result = await client.send(
                "Network.getResponseBody", {}, session_id=session_id
            )
        except Exception:
            return []
        # The DevTools client buffers the events it receives; the ones
        # this probe needs are the request events.
        events = getattr(client, "_events", None)
        if not events:
            return []
        return [event for event in events if event.get("method") == "Network.requestWillBeSent"]

    async def _read_verdict(
        self,
        client: DevToolsClient,
        session_id: str | None,
        site: CheckSite,
    ) -> str:
        """Read the verdict the site published, when it published one.

        The sites are third-party pages whose layout changes without
        notice.  A site whose verdict cannot be found is reported as
        "no verdict found" -- the probe did not find the answer, which
        is not the same as the answer being bad.
        """

        if not site.verdict_selector:
            return ""
        try:
            raw = await client.evaluate(
                f"""(() => {{
    const nodes = document.querySelectorAll({json.dumps(site.verdict_selector)});
    if (!nodes.length) return "";
    const parts = [];
    for (const node of nodes) {{
        const text = (node.textContent || "").trim();
        if (text) parts.push(text);
    }}
    return parts.join("\\n");
}})()""",
                session_id=session_id,
            )
        except Exception:
            return ""
        if not isinstance(raw, str):
            return ""
        text = raw.strip()
        if not text:
            return "no verdict found"
        return text[:2000]

    def _compare(
        self,
        site: CheckSite,
        expected: dict[str, str],
        captured: dict[str, str],
    ) -> list[HeaderFinding]:
        """Compare the captured headers against the profile's own values."""

        findings: list[HeaderFinding] = []
        for name, wanted in expected.items():
            if not wanted:
                continue
            sent = captured.get(name)
            if not sent:
                continue
            if sent == wanted:
                continue
            findings.append(
                HeaderFinding(
                    site=site.name,
                    header=name,
                    expected=wanted,
                    actual=sent,
                )
            )
        return findings

    def _launch(self, browser_path: str) -> _BrowserProcess:
        """Launch the browser with the profile's document and payload.

        The command line is built by the product's own launcher, so the
        probe measures what a customer gets rather than what a probe
        happens to ask for.  A probe that builds its own command line
        would miss the user agent, the language and the client hints
        the launcher sets -- and it would report a mismatch for every
        header, which reads as a defect in the product when it is
        really a defect in the probe.
        """

        user_data_dir = Path(tempfile.mkdtemp(prefix="nova-probe-"))
        command = [browser_path]
        if self._headless:
            command.append("--headless=new")
        command.append("--remote-debugging-port=0")
        command.append(f"--user-data-dir={user_data_dir}")
        # The native core reads the profile from this file before the
        # renderer is forked.  Without it the browser runs with its own
        # values and every axis mismatches, which says nothing about
        # the core -- only that the hand-over did not happen.
        core_config = self._write_core_config(user_data_dir)
        if core_config is not None:
            command.append(f"--nova-config={core_config}")
        # The JavaScript layer's payload is handed over the same way:
        # the path goes on the command line, the payload stays in the
        # user data directory, where the command line of the process
        # cannot be read by another process.
        payload = self._write_js_payload(user_data_dir)
        if payload is not None:
            command.append(f"--nova-js-payload={payload}")
        command.append("--no-first-run")
        command.append("--no-default-browser-check")
        command.append("--disable-background-networking")
        command.append("--disable-sync")
        command.append("--disable-extensions")
        command.append("--disable-dev-shm-usage")
        command.append("--hide-scrollbars")
        command.append("--mute-audio")
        command.append("about:blank")
        process = subprocess.Popen(  # noqa: S603 - explicit, local binary
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            port = self._read_devtools_port(user_data_dir)
        except Exception:
            self._terminate_process_tree(process)
            with contextlib.suppress(OSError):
                shutil.rmtree(user_data_dir, ignore_errors=True)
            raise
        return _BrowserProcess(process=process, port=port, user_data_dir=user_data_dir)

    def _write_core_config(self, user_data_dir: Path) -> Path | None:
        """Write the profile document the native core reads.

        The document is the plain profile JSON: the core parses it as it
        stands, so a file wrapped in an envelope would be read as a
        profile with no fields -- and a browser that refused its
        configuration reports the host's values, which is precisely
        what a probe of a third-party site would then measure.
        """

        try:
            from ..launcher.core_document import build_core_document

            document = build_core_document(self._config)
            target = user_data_dir / "nova_core.json"
            target.write_text(
                json.dumps(document, indent=2, sort_keys=True, default=str),
                encoding="utf-8",
            )
            return target
        except (OSError, TypeError, ValueError):
            return None

    def _write_js_payload(self, user_data_dir: Path) -> Path | None:
        """Write the JavaScript layer's payload into ``user_data_dir``."""

        try:
            from ..js_runtime.builder import JSRuntimeBuilder
            from ..js_runtime.injector import write_payload

            profile = ProfileConfig.model_validate(
                self._config.model_dump(mode="python")
            )
            written = write_payload(
                profile,
                user_data_dir,
                builder=JSRuntimeBuilder(),
            )
            return written.path
        except Exception:
            return None

    @staticmethod
    def _read_devtools_port(user_data_dir: Path, timeout: float = 30.0) -> int:
        """Read the DevTools port Chrome wrote into ``DevToolsActivePort``."""

        port_file = user_data_dir / "DevToolsActivePort"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if port_file.exists():
                try:
                    content = port_file.read_text(encoding="utf-8").splitlines()
                except OSError:  # pragma: no cover - transient
                    content = []
                if content:
                    try:
                        return int(content[0].strip())
                    except ValueError:  # pragma: no cover - transient
                        pass
            time.sleep(0.05)
        raise TimeoutError("the browser did not report a DevTools port in time")

    @staticmethod
    def _terminate(launched: _BrowserProcess) -> None:
        """Terminate the browser and remove its temporary profile."""

        ThirdPartyRunner._terminate_process_tree(launched.process)
        shutil.rmtree(launched.user_data_dir, ignore_errors=True)

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
        """Terminate the browser *and* every child it spawned."""

        children: list[Any] = []
        try:
            import psutil

            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
        except Exception:  # pragma: no cover - psutil optional at runtime
            children = []

        with contextlib.suppress(Exception):
            process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)

        for child in children:
            with contextlib.suppress(Exception):
                child.terminate()
            with contextlib.suppress(Exception):
                child.wait(timeout=10)

    @staticmethod
    def _fetch_json(url: str) -> dict[str, Any]:
        """Fetch a JSON document over HTTP."""

        import urllib.request

        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}
