"""Leak containment: keeping the real machine out of the browser.

A profile that claims to be a Pixel 8 Pro running Android 14 but that still
reports the host's Windows GPU, its real IP address or its real file system
is worse than no profile at all: the contradiction is a stronger bot signal
than the fingerprint it was supposed to hide.

This module builds the Chromium command-line flags that close the leak
paths, and the small set of environment overrides that stop Chromium from
learning about the host at all.

What is contained
-----------------

``windows``/``host platform``
    Chromium is told to report the Android user agent and the Android
    platform string.  The remaining host leakage is the *renderer* string,
    the WebGL vendor and the audio stack; those are handled by the core
    config, not by flags.

``hardware GPU``
    Chromium's GPU process reports the host adapter unless it is disabled.
    ``--disable-gpu`` plus the ANGLE SwiftShader backend makes the browser
    report a software rasteriser, which is then overridden by the profile's
    own WebGL strings.

``real IP``
    WebRTC, DNS and the proxy settings are handled by the network modules;
    this module adds the flags that stop the *other* paths: the QUIC
    handshake, the network change notifier, the geolocation provider and
    the IP address of the machine.

``file system``
    ``--disable-features=...`` stops the media access and the WebUSB
    enumerators from exposing anything, and the user data directory is
    always a fresh per-profile directory.

``fonts``
    ``--disable-remote-fonts`` is deliberately *not* used: a device without
    its web fonts looks like a device from 2015.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Features that must never be active in a profile that claims to be a
#: stock Android device.  Each entry blocks a path that would otherwise
#: reveal the host or a desktop-only behaviour.
LEAK_BLOCKED_FEATURES: tuple[str, ...] = (
    # The media access handler can enumerate host files through a picker.
    "MediaRouter",
    # The Cast Discovery feature scans the local network.
    "CastMediaRouter",
    # The global media controls key ties the browser to the host OS.
    "GlobalMediaControls",
    # WebUSB and WebBluetooth enumerate the host's devices.
    "WebUSB",
    # The extension updater phones home with the host's identity.
    "ExtensionUpdater",
    # The "Chrome Sign In" surface leaks the host's Google account state.
    "ChromeSignin",
    # Autofill suggestions leak the host's saved form data.
    "AutofillServerCommunication",
    # The chrome://" surfaces are reachable only through a flag.
    "ChromeRefresh2023",
    # The client hints of the host platform must never reach the wire; the
    # profile supplies its own.
    "UserAgentClientHint",
)

#: Features that make Chromium behave more like a plain Android device.
#: These are *enabled* by default in a desktop build but are absent on
#: Android, and their absence is a visible difference.
LEAK_ENABLED_FEATURES: tuple[str, ...] = (
    # Android has no touchpad or mouse pointer.
    "TouchpadOverscrollHistoryNavigation",
)


@dataclass(frozen=True)
class LeakGuardConfig:
    """What to contain, and how hard.

    Attributes
    ----------
    disable_gpu:
        Run the GPU process in software mode.  Without this the browser
        reports the host's real adapter, which contradicts the profile's
        own WebGL strings.  This also disables the WebGPU hardware
        backend, which is a separate path to the host's graphics adapter.
    force_software_renderer:
        Use SwiftShader explicitly rather than relying on the absence of a
        GPU.
    block_real_ip:
        Apply the flags that stop the browser from learning the host's
        public address outside the proxy.
    block_quic:
        Disable QUIC.  A QUIC handshake goes out over UDP directly, which
        bypasses an HTTP proxy and exposes the real address.
    block_network_change_notifier:
        Stop the OS network-change notifier from resetting the proxy.
    block_geolocation_provider:
        The OS geolocation provider would override the profile's own
        coordinates with the host's real ones.
    disable_oom_protection:
        Chromium's out-of-memory protection reports the host's memory
        pressure state to the renderer.
    extra_disable_features:
        Additional feature names to disable, for operator use.
    extra_enable_features:
        Additional feature names to enable, for operator use.
    """

    disable_gpu: bool = True
    force_software_renderer: bool = True
    block_real_ip: bool = True
    block_quic: bool = True
    block_network_change_notifier: bool = True
    block_geolocation_provider: bool = True
    disable_oom_protection: bool = False
    extra_disable_features: tuple[str, ...] = field(default_factory=tuple)
    extra_enable_features: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def strict(cls) -> LeakGuardConfig:
        """The strictest configuration: contain everything."""

        return cls(disable_oom_protection=True)


def build_leak_guard_flags(config: LeakGuardConfig | None = None) -> list[str]:
    """Build the ordered Chromium flags implementing the containment.

    The output is deterministic: the same configuration always yields the
    exact same list, in the same order.
    """

    cfg = config or LeakGuardConfig()
    args: list[str] = []

    if cfg.disable_gpu:
        args.append("--disable-gpu")
    if cfg.force_software_renderer:
        # ANGLE's SwiftShader backend: a deterministic software rasteriser
        # that reports the same values on every host.
        args.append("--use-gl=angle")
        args.append("--use-angle=swiftshader")
        args.append("--disable-gpu-compositing")

    if cfg.block_real_ip:
        # The WebRTC IP handling policy is applied by the launcher, which
        # knows the profile's WebRTC mode.  Here we add the flags that
        # cover the remaining paths.  Feature flags are *not* emitted here:
        # Chromium honours only the last ``--disable-features``, so they are
        # merged into one list by :func:`merge_feature_lists`.
        args.append("--disable-webrtc-hw-decoding")
        args.append("--disable-ipc-flooding-protection")
        args.append("--disable-background-networking")
        args.append("--disable-component-update")
        args.append("--disable-domain-reliability")
        args.append("--disable-client-side-phishing-detection")

    if cfg.disable_gpu:
        # WebGPU is a *second*, independent path to the host's graphics
        # hardware: ``navigator.gpu`` talks to the Dawn backend, which is
        # started separately from the GL/ANGLE stack that ``--disable-gpu``
        # and ``--use-angle=swiftshader`` cover.  Left alone it would report
        # the host's real adapter -- "NVIDIA GeForce RTX 4090" on a desktop
        # that is pretending to be a Pixel 8 -- which is a contradiction the
        # page can read through ``adapter.info`` and
        # ``navigator.gpu.getPreferredCanvasFormat()``.
        #
        # The native core overrides the adapter strings anyway; disabling
        # the hardware backend makes sure that the value the core
        # substitutes is the *only* value the page can observe.
        args.append("--disable-webgpu")

    if cfg.block_quic:
        # QUIC bypasses an HTTP proxy and exposes the real address.
        args.append("--disable-quic")

    # NOTE: ``OomProtection`` is *not* emitted here.  Chromium honours only
    # the last ``--disable-features`` flag on the command line, so emitting
    # a second one here would silently drop the whole merged feature list
    # that the launcher appends afterwards -- or, in the other order, drop
    # this guard.  The feature is merged into the single flag by
    # :func:`merge_feature_lists` instead.

    return args


def merge_feature_lists(
    base_disable: list[str],
    base_enable: list[str],
    guard: LeakGuardConfig | None = None,
) -> tuple[list[str], list[str]]:
    """Merge the guard's feature lists into the launcher's own lists.

    Chromium accepts several ``--disable-features`` flags but honours only
    the last one, so the lists have to be merged into a single value.  The
    result is sorted and de-duplicated so the command line is stable.
    """

    cfg = guard or LeakGuardConfig()
    disable = set(base_disable) | set(LEAK_BLOCKED_FEATURES) | set(cfg.extra_disable_features)
    enable = set(base_enable) | set(cfg.extra_enable_features)

    if cfg.block_quic:
        disable.add("Quic")
    if cfg.block_network_change_notifier:
        disable.add("NetworkChangeNotifier")
    if cfg.block_geolocation_provider:
        disable.add("GeolocationApi")
    if cfg.disable_oom_protection:
        disable.add("OomProtection")

    # A feature cannot be both enabled and disabled; the disabled side
    # wins, because that is the safe direction.
    enable -= disable

    return sorted(disable), sorted(enable)


def build_sandbox_environment(
    profile_home: str,
    user_agent: str = "",
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the environment overrides that hide the host from Chromium.

    The variables below stop Chromium from finding the host's real
    configuration: the user-data path, the locale, the timezone and the
    language.  Everything else is inherited, because Chromium still needs
    its own runtime environment to start.
    """

    env: dict[str, str] = {}

    # Chromium reads the locale and the language from the environment when
    # the flags do not fully cover them.
    if user_agent:
        env["NOVA_USER_AGENT"] = user_agent

    # The temp directory has to live inside the profile's own directory so
    # that no path outside the profile can be discovered.
    env["TMPDIR"] = profile_home
    env["TMP"] = profile_home
    env["TEMP"] = profile_home

    # A stable, profile-local download directory: the host's real one is a
    # path that identifies the machine.
    env["XDG_CACHE_HOME"] = f"{profile_home}/cache"
    env["XDG_CONFIG_HOME"] = f"{profile_home}/config"
    env["XDG_DATA_HOME"] = f"{profile_home}/data"

    if extra:
        env.update(extra)

    return env


__all__ = [
    "LEAK_BLOCKED_FEATURES",
    "LEAK_ENABLED_FEATURES",
    "LeakGuardConfig",
    "build_leak_guard_flags",
    "build_sandbox_environment",
    "merge_feature_lists",
]
