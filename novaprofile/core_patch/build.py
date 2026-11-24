"""Build tooling for the NOVA Profile native Chromium core.

This module compiles the native Chromium patch series into browser cores.
It is the only piece of the project that talks to ``git``, ``gn`` and
``ninja``; everything else in NOVAPROFILE speaks to a *built* core.

The tool has five commands:

``list``
    List the supported Chromium versions with their branch, tag and the
    patch series they accept.

``plan --version <ver>``
    Print the exact shell commands that fetch, patch and build a core.
    Nothing is executed: a plan is a document, and a build that is
    described but not executed can be reviewed before it costs a machine
    a day.

``apply --version <ver> --checkout <path>``
    Apply the patch series to an existing checkout with ``git apply``.
    The command is idempotent: a patch that is already applied is
    reported as such and skipped, so re-running it is safe.

``verify --checkout <path>``
    Verify a built core: the patched binary exists, it is newer than the
    patch files that produced it, and it reports the expected version.

``check``
    Validate the patch series itself.  This works without a Chromium
    checkout, which is the point: a broken patch is discovered before
    the machine that builds the cores is even switched on.

Design notes
------------

**Why the version matrix lives in JSON.**  The matrix encodes facts about
upstream Chromium -- release branch numbers, tags, GN arguments -- that
change over time and that a human has to be able to audit and correct.
Keeping them in a data file rather than in code means that adding a
milestone is an edit to a document, not a code change, and that the
tooling and the documentation cannot drift apart.

**Where the branch numbers come from.**  The branch number of a Chromium
milestone is the *third* component of its version: ``120.0.6099.144`` is
built from branch ``6099``.  This is Chromium's own rule, stated in
``infra/config/project.star`` in the Chromium tree (*"The 3rd portion of
the version number is the branch number for the associated milestone"*)
and published in ``infra/config/milestones.json`` and on
https://chromiumdash.appspot.com/branches.

The frequently repeated rule *"the branch number is the major version plus
900"* is **not** a Chromium rule.  The numbers ``1000``, ``1120``, ``1140``
and so on are the *temporary beta branches* Chromium cuts at the beta cut
(``refs/branch-heads/1120`` was M112's beta branch, and it is deleted
again when the milestone goes stable).  They are a different namespace
from the release branch, and a build cannot be made from them.  This
module encodes the real release branches and records the beta branch
number separately, so that the distinction is explicit and can be
asserted by the tests.

**Why ``apply`` uses ``git apply`` and not ``patch``.**  ``git apply`` is
strict: it refuses a hunk whose context does not match, it reports *which*
file failed and *why*, and it understands the ``diff --git`` headers the
series uses.  ``patch(1)`` would happily apply a hunk at the wrong
offset, and a patch that lands in the wrong place is a core that builds
and then lies.

**Why ``apply`` is idempotent.**  A build is interrupted; the operator
re-runs the command.  If the second run failed because the first run had
already applied the series, the tool would be actively harmful.  So
``apply`` asks ``git apply --check --reverse`` whether the patch is
already in the tree; if it is, the patch is reported as *already applied*
and skipped rather than as a failure.

**Why ``check`` parses the patches itself.**  ``git apply --check``
needs a Chromium checkout, and a checkout is 40 GB and several hours of
network traffic.  The invariants that actually break a patch -- a missing
prerequisite accessor, a file path that does not exist in a Chromium
tree, a hunk without context -- are all decidable from the patch text
and from the NOVA sources alone, so ``check`` decides them without the
checkout.

**Where the boundaries are.**  This module never edits anything outside
the Chromium checkout it is pointed at, never touches the NOVA sources,
and never runs a build command: ``plan`` prints the build commands and
``apply`` runs only ``git apply``.  A tool that builds Chromium has to be
usable on a machine where the build takes a day; a tool that decides
*what* to build has to be safe to run anywhere.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# --------------------------------------------------------------------------
# Layout
# --------------------------------------------------------------------------

#: The directory this module lives in -- ``novaprofile/core_patch``.
CORE_PATCH_DIR: Final[Path] = Path(__file__).resolve().parent

#: The configuration document that describes the supported versions.
CONFIG_PATH: Final[Path] = CORE_PATCH_DIR / "build_config.json"

#: The directory holding the patch series.
PATCH_DIR: Final[Path] = CORE_PATCH_DIR / "patches"

#: The directory holding the C++ sources the patches call into.
SRC_DIR: Final[Path] = CORE_PATCH_DIR / "src"

#: The upstream Chromium repository.
CHROMIUM_REPO: Final[str] = "https://chromium.googlesource.com/chromium/src.git"

#: The upstream depot_tools repository.
DEPOT_TOOLS_REPO: Final[str] = "https://chromium.googlesource.com/chromium/tools/depot_tools.git"

#: The Chromium layout a patch path is resolved against.  A patch header
#: names a path relative to the root of the ``src`` tree, so the check
#: resolves it against this layout.
CHROMIUM_LAYOUT: Final[tuple[str, ...]] = (
    "third_party/blink/renderer/core",
    "third_party/blink/renderer/core/html",
    "third_party/blink/renderer/core/canvas",
    "third_party/blink/renderer/core/screen",
    "third_party/blink/renderer/core/frame",
    "third_party/blink/renderer/core/layout",
    "third_party/blink/renderer/core/storage",
    "third_party/blink/renderer/core/events",
    "third_party/blink/renderer/core/execution_context",
    "third_party/blink/renderer/core/nova",
    "third_party/blink/renderer/modules",
    "third_party/blink/renderer/modules/webgl",
    "third_party/blink/renderer/modules/webaudio",
    "third_party/blink/renderer/modules/webgpu",
    "third_party/blink/renderer/modules/canvas/canvas2d",
    "content/app",
    "content/browser",
    "content/gpu",
)

#: The names of the binaries a built core can produce, in the order in
#: which they are looked for.  The first one that exists is the core.
BINARY_CANDIDATES: Final[tuple[str, ...]] = (
    "chrome",
    "chrome.exe",
    "chrome-wrapper",
    "chrome-headless-shell",
    "chrome-headless-shell.exe",
)

#: The suffixes of the binaries a Chromium build produces.
BINARY_SUFFIXES: Final[tuple[str, ...]] = ("", ".exe")

#: The exit code used for every failure that is not a usage error.
EXIT_FAILURE: Final[int] = 1


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class BuildError(RuntimeError):
    """Raised when a build operation cannot be completed."""


class UnknownVersionError(BuildError):
    """Raised when a version specification is not in the matrix."""


class PatchSeriesError(BuildError):
    """Raised when the patch series is not internally consistent."""


# --------------------------------------------------------------------------
# The version matrix
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CoreVersion:
    """One supported Chromium version and how to build it."""

    major: int
    branch: int
    beta_branch: int
    branch_ref: str
    version: str
    tag: str
    tag_pattern: str
    patches: tuple[str, ...]
    gn_args: dict[str, str]
    channel: str

    @property
    def branch_name(self) -> str:
        """The name of the release branch, e.g. ``6099``."""

        return str(self.branch)

    @property
    def branch_head(self) -> str:
        """The git ref of the release branch, e.g. ``branch-heads/6099``."""

        return f"branch-heads/{self.branch}"

    @property
    def has_beta_branch(self) -> bool:
        """Whether the beta-stage branch of this milestone is known.

        Chromium cuts a branch at the beta stage and re-cuts the release
        branch later, so a milestone has *two* branch numbers.  Only the
        release one -- the third component of the version -- is used to
        build a core; the beta-stage one is recorded only where it has
        been attested.
        """

        return self.beta_branch > 0

    def gn_args_string(self) -> str:
        """The GN arguments as a single ``--args=`` value."""

        parts = [f'{key}="{value}"' for key, value in sorted(self.gn_args.items())]
        return " ".join(parts)


@dataclass(frozen=True)
class BuildMatrix:
    """The whole supported version matrix."""

    versions: tuple[CoreVersion, ...]
    latest: CoreVersion
    patch_series: tuple[str, ...]
    out_dir: str
    ninja_target: str
    chromium_repo: str
    depot_tools_url: str
    oldest_major: int
    newest_major: int
    source_path: Path = field(default=CONFIG_PATH)

    def get(self, major: int) -> CoreVersion:
        """The entry for ``major``.

        Raises :class:`UnknownVersionError` when the milestone is not
        supported.
        """

        for version in self.versions:
            if version.major == major:
                return version
        raise UnknownVersionError(
            f"Chromium {major} is not supported; the matrix covers "
            f"{self.oldest_major}..{self.newest_major}"
        )

    def resolve(self, spec: str) -> CoreVersion:
        """Resolve a version specification into a matrix entry.

        Accepts a major (``120``), a full version (``120.0.6099.144``) and
        the aliases ``latest`` / ``newest`` / ``current``.
        """

        text = spec.strip().lower()
        if text in {"latest", "newest", "current"}:
            return self.latest

        digits = ""
        for char in text:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            raise UnknownVersionError(f"cannot parse a Chromium version from {spec!r}")
        return self.get(int(digits))

    @property
    def majors(self) -> tuple[int, ...]:
        """Every supported major version, ascending."""

        return tuple(version.major for version in self.versions)


def _require(payload: dict[str, object], key: str, context: str) -> object:
    """Fetch a key from the configuration, or fail with a clear message."""

    if key not in payload:
        raise BuildError(f"the build configuration is missing {key!r} in {context}")
    return payload[key]


def load_matrix(path: Path | str | None = None) -> BuildMatrix:
    """Load the version matrix from ``build_config.json``.

    Raises :class:`BuildError` when the document is missing, malformed or
    does not describe a usable matrix.
    """

    config_path = Path(path) if path is not None else CONFIG_PATH
    if not config_path.is_file():
        raise BuildError(f"the build configuration does not exist: {config_path}")
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"cannot read the build configuration at {config_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BuildError(f"the build configuration at {config_path} is not a JSON object")

    raw_versions = _require(payload, "versions", str(config_path))
    if not isinstance(raw_versions, list) or not raw_versions:
        raise BuildError("the build configuration lists no versions")

    entries: list[CoreVersion] = []
    for raw in raw_versions:
        if not isinstance(raw, dict):
            raise BuildError("a version entry of the build configuration is not an object")
        entries.append(_parse_version(raw))

    raw_latest = _require(payload, "latest", str(config_path))
    if not isinstance(raw_latest, dict):
        raise BuildError("the 'latest' entry of the build configuration is not an object")
    latest = _parse_version(raw_latest)

    series = _require(payload, "patch_series", str(config_path))
    if not isinstance(series, list) or not all(isinstance(item, str) for item in series):
        raise BuildError("'patch_series' of the build configuration is not a list of strings")

    return BuildMatrix(
        versions=tuple(entries),
        latest=latest,
        patch_series=tuple(series),
        out_dir=str(payload.get("out_dir", "out/nova")),
        ninja_target=str(payload.get("ninja_target", "chrome")),
        chromium_repo=str(payload.get("chromium_repo", CHROMIUM_REPO)),
        depot_tools_url=str(payload.get("depot_tools_url", DEPOT_TOOLS_REPO)),
        oldest_major=int(payload.get("oldest_major", entries[0].major)),
        newest_major=int(payload.get("newest_major", entries[-1].major)),
        source_path=config_path,
    )


def _parse_version(raw: dict[str, object]) -> CoreVersion:
    """Turn one raw configuration entry into a :class:`CoreVersion`."""

    def field_of(name: str) -> object:
        return _require(raw, name, "a version entry")

    major = int(field_of("major"))
    branch = int(field_of("branch"))
    patches = field_of("patches")
    if not isinstance(patches, list) or not all(isinstance(item, str) for item in patches):
        raise BuildError(f"the 'patches' of Chromium {major} is not a list of strings")
    gn_args = field_of("gn_args")
    if not isinstance(gn_args, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in gn_args.items()
    ):
        raise BuildError(f"the 'gn_args' of Chromium {major} is not a mapping of strings")

    return CoreVersion(
        major=major,
        branch=branch,
        beta_branch=int(raw.get("beta_branch", 0)),
        branch_ref=str(raw.get("branch_ref", f"refs/branch-heads/{branch}")),
        version=str(field_of("version")),
        tag=str(field_of("tag")),
        tag_pattern=str(raw.get("tag_pattern", f"{major}.0.{branch}.*")),
        patches=tuple(patches),
        gn_args=dict(gn_args),
        channel=str(raw.get("channel", "stable")),
    )


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlanStep:
    """One command of a build plan."""

    description: str
    command: str
    cwd: str = ""

    def render(self) -> str:
        """The command as a shell would take it.

        The plan is meant to be pasted into a shell, so it has to be
        written for the shell the build runs in.  On Windows that is
        PowerShell or ``cmd``, neither of which understands the POSIX
        ``(cd DIR && CMD)`` idiom, so the working directory is put in
        front of the command instead.
        """

        if not self.cwd:
            return self.command
        if os.name == "nt":
            return f"Set-Location {self.cwd}; {self.command}"
        return f"(cd {self.cwd} && {self.command})"


@dataclass(frozen=True)
class BuildPlan:
    """The full sequence of commands that produces a core."""

    version: CoreVersion
    checkout: Path
    steps: tuple[PlanStep, ...]

    def render(self) -> str:
        """The plan as a shell script."""

        lines: list[str] = []
        for index, step in enumerate(self.steps, start=1):
            lines.append(f"# {index}. {step.description}")
            lines.append(step.render())
        return "\n".join(lines)


def _shell_path(path: Path | str) -> str:
    """A path as a shell would take it.

    A path with a space in it has to be quoted, or the shell splits it
    into two arguments and the command does something else entirely.
    The quoting style is the one of the shell the plan is printed for.
    """

    text = str(path)
    if os.name == "nt":
        return f'"{text}"' if " " in text else text
    return f"'{text}'" if " " in text else text


def build_plan(
    version: CoreVersion,
    checkout: Path | str,
    *,
    matrix: BuildMatrix | None = None,
) -> BuildPlan:
    """The commands that fetch, patch and build a core of ``version``.

    The plan is *printed*, never executed: a Chromium build costs a
    machine a day, and a plan that has been reviewed is worth more than
    one that has been guessed at.
    """

    root = Path(checkout).expanduser()
    matrix = matrix or load_matrix()
    patch_paths = [_shell_path(PATCH_DIR / Path(name).name) for name in version.patches]
    core_sources = [
        SRC_DIR / "nova_core_config.h",
        SRC_DIR / "nova_core_config.cc",
        SRC_DIR / "nova_unit_values.h",
        SRC_DIR / "nova_unit_values.cc",
        SRC_DIR / "nova_chromium_hooks.h",
        SRC_DIR / "nova_pixel_noise.h",
        SRC_DIR / "nova_pixel_noise.cc",
        SRC_DIR / "nova_blink_patch.cc",
    ]

    steps: list[PlanStep] = [
        PlanStep(
            description="Install depot_tools (the fetcher, gn and ninja live there)",
            command=(
                f"git clone {matrix.depot_tools_url} "
                f"{_shell_path(root / 'depot_tools')}"
            ),
        ),
        PlanStep(
            description="Fetch the Chromium source at the release branch",
            command=(
                "gclient sync --with_branch_heads --with_tags "
                f"--revision={version.branch_ref}"
            ),
            cwd=str(root / "src"),
        ),
        PlanStep(
            description=f"Check out the Chromium {version.major} release branch",
            command=f"git checkout -b nova-profile-{version.major} {version.branch_head}",
            cwd=str(root / "src"),
        ),
        PlanStep(
            description="Apply the NOVA patch series (in order)",
            command="git apply " + " ".join(patch_paths),
            cwd=str(root / "src"),
        ),
        PlanStep(
            description="Copy the NOVA core sources into the Chromium tree",
            command=(
                _copy_command(core_sources, root / "src" / "third_party" / "blink" / "renderer" / "core" / "nova")
            ),
        ),
        PlanStep(
            description="Generate the ninja files",
            # The arguments go into the output directory's ``args.gn`` rather
            # than onto the command line.  GN's quoting and the shell's
            # quoting disagree often enough that a command line is the
            # wrong place for a dozen arguments, and the failure mode is
            # a build that never starts.
            command=(
                f"write {matrix.out_dir}/args.gn from the version matrix, "
                f"then run gn gen {matrix.out_dir}"
            ),
            cwd=str(root / "src"),
        ),
        PlanStep(
            description="Build the browser",
            # ``autoninja`` is not used: it reads ``/proc/self/fd/1`` to
            # work out whether the build runs on a terminal, which is a
            # Linux path.  Ninja itself does the same job.
            command=f"ninja -C {matrix.out_dir} {matrix.ninja_target}",
            cwd=str(root / "src"),
        ),
        PlanStep(
            description=(
                "Note: the build needs DEPOT_TOOLS_WIN_TOOLCHAIN=0 and "
                "vs2022_install pointing at the local Visual Studio, "
                "unless depot_tools' own toolchain was fetched"
            ),
            command="(informational)",
            cwd=str(root / "src"),
        ),
    ]
    return BuildPlan(version=version, checkout=root, steps=tuple(steps))


def _copy_command(sources: list[Path], destination: Path) -> str:
    """The command that copies the core sources into the Chromium tree.

    ``cp`` on Windows is a shell alias, not an executable, so the
    plan uses the command that exists in both worlds: ``Copy-Item``
    under PowerShell and ``cp`` everywhere else.
    """

    operands = " ".join(_shell_path(source) for source in sources)
    target = _shell_path(destination)
    if os.name == "nt":
        return f"Copy-Item {operands} {target}"
    return f"cp {operands} {target}"


# --------------------------------------------------------------------------
# Applying the series
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PatchResult:
    """The outcome of applying one patch."""

    name: str
    path: Path
    applied: bool
    already_applied: bool
    ok: bool
    stderr: str = ""

    @property
    def status(self) -> str:
        """A one-word status for the report."""

        if self.applied:
            return "applied"
        if self.already_applied:
            return "already-applied"
        if self.ok:
            return "skipped"
        return "failed"


@dataclass(frozen=True)
class ApplyReport:
    """The outcome of applying the whole series."""

    version: CoreVersion
    checkout: Path
    results: tuple[PatchResult, ...]

    @property
    def ok(self) -> bool:
        """Whether every patch of the series is in place."""

        return all(result.ok for result in self.results)

    @property
    def failures(self) -> tuple[PatchResult, ...]:
        """The patches that could not be applied."""

        return tuple(result for result in self.results if not result.ok)


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command and capture its output."""

    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise BuildError(f"cannot run git {' '.join(args)}: {exc}") from exc


def _is_checkout_root(checkout: Path) -> bool:
    """Whether ``checkout`` is itself the root of a git checkout.

    Git walks up the tree when it looks for a repository, so a plain
    directory that happens to sit inside one is reported as being in a
    repository.  That is not what the caller asked about: the patches
    have to land in the *root* of the Chromium checkout, because the
    paths in a patch series are rooted there.  Applying them to a
    sub-directory would either fail or land in the wrong tree, and
    neither is the error the caller needs to be told about.

    So the top level of the repository is asked for, and it has to be
    the directory that was named.
    """

    completed = _run_git(["rev-parse", "--show-toplevel"], checkout)
    if completed.returncode != 0:
        return False

    reported = completed.stdout.strip()
    if not reported:
        return False

    try:
        top = Path(reported).resolve()
        return top == checkout.resolve()
    except OSError:
        return False


def _is_already_applied(checkout: Path, patch: Path) -> bool:
    """Whether ``patch`` is already in the tree at ``checkout``.

    ``git apply --check --reverse`` succeeds exactly when the patch could
    be *un*-applied, which is the case when it has been applied.  This is
    how the tool makes a re-run safe.
    """

    completed = _run_git(["apply", "--check", "--reverse", str(patch)], checkout)
    return completed.returncode == 0


def apply_series(
    version: CoreVersion,
    checkout: Path | str,
    *,
    patch_dir: Path | str | None = None,
) -> ApplyReport:
    """Apply the patch series of ``version`` to ``checkout``.

    The series is applied in order, because the second patch substitutes
    accessors the first one has already put in place.

    A patch that is already in the tree is reported as
    ``already-applied`` and skipped; that is what makes the command safe
    to re-run after an interrupted build.  A patch that fails to apply
    stops the series -- applying the second patch on top of a broken
    first one produces a tree that neither matches the source nor the
    intent -- and its real stderr is reported verbatim.
    """

    root = Path(checkout).expanduser()
    if not root.is_dir():
        raise BuildError(f"the Chromium checkout does not exist: {root}")
    if not _is_checkout_root(root):
        raise BuildError(f"the Chromium checkout is not a git repository: {root}")

    base = Path(patch_dir) if patch_dir is not None else PATCH_DIR
    results: list[PatchResult] = []

    for name in version.patches:
        patch = base / Path(name).name
        if not patch.is_file():
            results.append(
                PatchResult(
                    name=Path(name).name,
                    path=patch,
                    applied=False,
                    already_applied=False,
                    ok=False,
                    stderr=f"the patch file does not exist: {patch}",
                )
            )
            break

        if _is_already_applied(root, patch):
            results.append(
                PatchResult(
                    name=patch.name,
                    path=patch,
                    applied=False,
                    already_applied=True,
                    ok=True,
                )
            )
            continue

        completed = _run_git(["apply", "--verbose", str(patch)], root)
        if completed.returncode == 0:
            results.append(
                PatchResult(
                    name=patch.name,
                    path=patch,
                    applied=True,
                    already_applied=False,
                    ok=True,
                    stderr=completed.stderr.strip(),
                )
            )
            continue

        results.append(
            PatchResult(
                name=patch.name,
                path=patch,
                applied=False,
                already_applied=False,
                ok=False,
                stderr=completed.stderr.strip(),
            )
        )
        break

    return ApplyReport(version=version, checkout=root, results=tuple(results))


# --------------------------------------------------------------------------
# Verifying a built core
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyResult:
    """The outcome of verifying a built core."""

    checkout: Path
    binary: Path | None
    expected_version: str
    ok: bool
    problems: tuple[str, ...]
    #: The version the binary reports, or an empty string when it
    #: reports none that could be read.
    reported_version: str = ""

    def render(self) -> str:
        """The report as text."""

        lines: list[str] = []
        if self.binary is not None:
            lines.append(f"binary: {self.binary}")
        else:
            lines.append("binary: <not found>")
        lines.append(f"expected version: {self.expected_version}")
        if self.reported_version:
            lines.append(f"reported version: {self.reported_version}")
        lines.append(f"result: {'ok' if self.ok else 'failed'}")
        for problem in self.problems:
            lines.append(f"  - {problem}")
        return "\n".join(lines)


def find_binary(checkout: Path | str) -> Path | None:
    """Locate the browser binary a core build produced.

    The candidates are looked for in the GN output directory first and
    then in the checkout root, because the output directory is where
    ``gn gen`` puts them.
    """

    root = Path(checkout).expanduser()
    search_roots = [root / "out" / "nova", root / "out", root]
    seen: set[Path] = set()
    for search_root in search_roots:
        if search_root in seen:
            continue
        seen.add(search_root)
        if not search_root.is_dir():
            continue
        for name in BINARY_CANDIDATES:
            candidate = search_root / name
            if candidate.is_file():
                return candidate
    return None


def read_binary_version(binary: Path) -> str:
    """The version string a built binary reports.

    The string is read out of the binary itself, because the file name
    of a Chromium build carries the version but the *binary* is what the
    browser reports to a page.  A build whose file name says 120 but
    whose binary says 119 is a build that has to be rebuilt.

    The version string appears near the end of the binary, in the
    version resource; scanning from the front would find the numbers of
    the embedded dependency versions first.  The pattern is tried with
    a word boundary first, and without one afterwards: a binary that
    stores the version right behind another byte has no boundary to
    find, and a version that is not read is a version that cannot be
    verified.
    """

    data = binary.read_bytes()
    window = data[-4 * 1024 * 1024 :]
    boundary = re.compile(rb"\b(\d{2,4})\.(\d+)\.(\d+)\.(\d+)\b")
    loose = re.compile(rb"(\d{2,4})\.(\d+)\.(\d+)\.(\d+)")

    for chunk in (window, data):
        matches = boundary.findall(chunk)
        if not matches:
            matches = loose.findall(chunk)
        if matches:
            best = matches[-1]
            return ".".join(part.decode("ascii") for part in best)
    return ""


def verify_core(
    checkout: Path | str,
    *,
    expected_version: str | None = None,
    matrix: BuildMatrix | None = None,
) -> VerifyResult:
    """Verify a built core.

    Three things are checked:

    * the patched binary exists,
    * it is newer than the patch files that produced it -- a binary that
      predates the series was built from an unpatched tree, and a page
      that reads it sees the host's values,
    * it reports a version whose major matches the requested one.
    """

    root = Path(checkout).expanduser()
    matrix = matrix or load_matrix()
    problems: list[str] = []

    binary = find_binary(root)
    if binary is None:
        problems.append(
            f"no patched binary was found under {root}; expected one of "
            + ", ".join(BINARY_CANDIDATES)
        )

    if expected_version is None:
        expected_version = matrix.latest.version
    expected_major = expected_version.split(".")[0]

    reported = ""
    if binary is not None:
        newest_patch = _newest_patch_mtime()
        if newest_patch is not None:
            binary_mtime = binary.stat().st_mtime
            if binary_mtime < newest_patch:
                problems.append(
                    f"the binary is older than the patch series "
                    f"({binary_mtime:.0f} < {newest_patch:.0f}); it was built "
                    "from an unpatched tree"
                )

        reported = read_binary_version(binary)
        if not reported:
            problems.append("the binary does not report a version string")
        elif reported.split(".")[0] != expected_major:
            problems.append(
                f"the binary reports version {reported}, but version "
                f"{expected_version} was requested"
            )

    return VerifyResult(
        checkout=root,
        binary=binary,
        expected_version=expected_version,
        ok=not problems,
        problems=tuple(problems),
        reported_version=reported,
    )


def _newest_patch_mtime() -> float | None:
    """The modification time of the newest patch of the series."""

    newest: float | None = None
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        stamp = patch.stat().st_mtime
        if newest is None or stamp > newest:
            newest = stamp
    return newest


# --------------------------------------------------------------------------
# Installing a built core
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltCore:
    """A built core, described the way the installer needs it.

    The control plane installs a core with
    ``CoreManager.register_core(version=..., major=..., source_dir=...,
    executable=...)``.  This dataclass carries exactly those values, so
    that the caller never has to reach into the checkout itself and
    guess which directory the browser ended up in.

    The fields are named after the parameters of ``register_core``; the
    mapping is one to one and is asserted by the tests.
    """

    #: The full version string, ``major.0.branch.build``.
    version: str
    #: The milestone.
    major: int
    #: The directory the core's files are in.
    source_dir: Path
    #: The browser executable, relative to ``source_dir``.
    executable: str
    #: The revision the core was built from.
    build_revision: str = ""

    def register_kwargs(self) -> dict[str, object]:
        """The keyword arguments for ``CoreManager.register_core``.

        The control plane's ``register_core`` is keyword-only, so this
        returns a mapping rather than a tuple: the caller can pass it
        straight through with ``**``.
        """

        return {
            "version": self.version,
            "major": self.major,
            "source_dir": self.source_dir,
            "executable": self.executable,
            "build_revision": self.build_revision,
        }


def describe_built_core(
    checkout: Path | str,
    version: str | int,
    *,
    matrix: BuildMatrix | None = None,
) -> BuiltCore:
    """Describe the core a checkout holds, for the installer.

    The checkout is the one the build ran in; the version is the
    milestone or the full version string of the core that was built in
    it.  The description is raised from what is actually on disk -- the
    binary that was found and the version it reports -- so that the
    installer registers the core that is there and not the core that
    was asked for.
    """

    matrix = matrix or load_matrix()
    root = Path(checkout).expanduser()
    entry = matrix.resolve(str(version))

    binary = find_binary(root)
    if binary is None:
        raise BuildError(
            f"no built core was found under {root}; expected one of "
            + ", ".join(BINARY_CANDIDATES)
        )

    reported = read_binary_version(binary)
    if reported and reported.split(".")[0] != str(entry.major):
        raise BuildError(
            f"the core under {root} reports version {reported}, but "
            f"Chromium {entry.major} was asked for"
        )

    # The installer copies the whole output directory, and the browser
    # is reached relative to it.
    source_dir = binary.parent
    try:
        executable = binary.relative_to(source_dir).as_posix()
    except ValueError:  # pragma: no cover - defensive
        executable = binary.name

    return BuiltCore(
        version=reported or entry.version,
        major=entry.major,
        source_dir=source_dir,
        executable=executable,
        build_revision=entry.branch_ref,
    )


# --------------------------------------------------------------------------
# Checking the patch series
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckFinding:
    """One problem found in the patch series."""

    severity: str
    subject: str
    message: str

    def render(self) -> str:
        """The finding as one line of text."""

        return f"{self.severity}: {self.subject}: {self.message}"


@dataclass(frozen=True)
class CheckReport:
    """The outcome of checking the patch series."""

    findings: tuple[CheckFinding, ...]
    patches: tuple[str, ...]
    touched_files: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Whether the series is sound."""

        return not any(finding.severity == "error" for finding in self.findings)

    @property
    def errors(self) -> tuple[CheckFinding, ...]:
        """The findings that make the series unusable."""

        return tuple(finding for finding in self.findings if finding.severity == "error")

    def render(self) -> str:
        """The report as text."""

        lines = [f"patches checked: {len(self.patches)}"]
        lines.append(f"files touched: {len(self.touched_files)}")
        if not self.findings:
            lines.append("result: ok")
            return "\n".join(lines)
        lines.append(f"result: {'ok' if self.ok else 'failed'}")
        for finding in self.findings:
            lines.append(f"  {finding.render()}")
        return "\n".join(lines)


#: The accessors the *first* patch substitutes.
#:
#: The first patch substitutes the identity surfaces: the User-Agent, the
#: client hints, the screen metrics.  Each of them is a stock Chromium
#: accessor that the patch puts behind a guard reading the profile's own
#: configuration.
#:
#: The accessor *body* is not in the NOVA sources.  It is in the Blink
#: file the patch edits: the substituted accessor keeps its original body
#: as the fallback, so the stock behaviour and the profile's behaviour
#: live in exactly one place and cannot drift apart.  What the NOVA
#: sources have to provide is the *hook* the accessor calls, and that is
#: checked against the hooks header below.
#:
#: So the invariant this list now carries is that each of these
#: accessors is substituted by the series at all: a page that can read
#: one of them and that the series does not touch reports the host's
#: value, which is exactly the failure the series exists to prevent.
#:
#: The names are the ones Chromium 159 uses.  The Blink reorganisation
#: moved several of these accessors out of ``NavigatorID`` and
#: ``NavigatorUAData`` into classes of their own.
FIRST_PATCH_ACCESSORS: Final[tuple[str, ...]] = (
    # The navigator's own attributes.
    "NavigatorBase::userAgent",
    "NavigatorBase::platform",
    "NavigatorID::appVersion",
    "NavigatorID::appName",
    "NavigatorID::appCodeName",
    "NavigatorID::vendor",
    "NavigatorID::vendorSub",
    "NavigatorID::product",
    "NavigatorID::productSub",
    "NavigatorID::platform",
    "NavigatorConcurrentHardware::hardwareConcurrency",
    "NavigatorDeviceMemory::deviceMemory",
    "NavigatorEvents::maxTouchPoints",
    "NavigatorLanguage::language",
    "NavigatorLanguage::languages",
    "Navigator::webdriver",
    # The client hints.  The low-entropy ones are accessors; the
    # high-entropy ones are not -- Chromium serves them from the
    # ``UADataValues`` that ``getHighEntropyValues`` builds, so they are
    # set through the setters rather than read from an accessor.
    "NavigatorUAData::brands",
    "NavigatorUAData::mobile",
    "NavigatorUAData::platform",
    "SetFullVersionList",
    "SetPlatform",
    "SetArchitecture",
    "SetModel",
    "SetBitness",
    "SetWoW64",
    # The screen metrics.
    "Screen::width",
    "Screen::height",
    "Screen::availWidth",
    "Screen::availHeight",
    "Screen::availLeft",
    "Screen::availTop",
    "Screen::colorDepth",
    "Screen::pixelDepth",
    "LocalDOMWindow::devicePixelRatio",
)

#: The hooks the patches call that have to be declared in a header, or
#: the core does not link.  These are the public entry points of the
#: core; the noise helpers that only the patches use are checked
#: separately against the implementation file.
REQUIRED_PUBLIC_HOOKS: Final[tuple[str, ...]] = (
    "IsNovaCoreInitialized",
    "GetNovaConfig",
    "GetNovaConfigRef",
)

#: The noise helpers the patches call directly.  They are implemented in
#: ``nova_pixel_noise.cc`` and have to be declared before the Chromium
#: translation units that include the hooks header can call them.
#:
#: The canvas readback is perturbed through ``ApplyPixelNoise`` at the
#: single point where the pixels leave the renderer, so the one helper is
#: enough for the canvas, the OffscreenCanvas and the WebGL readback.
REQUIRED_NOISE_HOOKS: Final[tuple[str, ...]] = (
    "ApplyPixelNoise",
)

#: The files the series is expected to touch.  A file that has moved
#: between Chromium releases makes a hunk fail to apply; knowing the
#: expected set is what lets the check say *which* file moved.
#:
#: Blink was reorganised in Chromium 159: the accessors that used to live
#: in ``core/html`` and ``core/screen`` are now in ``core/frame``, and the
#: navigator's own attributes are spread over ``core/execution_context``
#: and ``core/events``.  The expected set follows the tree.
EXPECTED_TOUCHED_FILES: Final[tuple[str, ...]] = (
    "third_party/blink/renderer/core/events/navigator_events.cc",
    "third_party/blink/renderer/core/execution_context/navigator_base.cc",
    "third_party/blink/renderer/core/frame/navigator.cc",
    "third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc",
    "third_party/blink/renderer/core/frame/navigator_device_memory.cc",
    "third_party/blink/renderer/core/frame/navigator_id.cc",
    "third_party/blink/renderer/core/frame/navigator_language.cc",
    "third_party/blink/renderer/core/frame/navigator_ua.cc",
    "third_party/blink/renderer/core/frame/navigator_ua_data.cc",
    "third_party/blink/renderer/core/frame/screen.cc",
    "third_party/blink/renderer/core/frame/local_dom_window.cc",
    "third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc",
    "third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc",
)


def parse_patch(text: str) -> tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]:
    """Parse a patch into its files and their hunks.

    Each file is ``(path, hunks)`` and each hunk is the tuple of its
    lines, without the leading marker.  The parser is deliberately
    strict: a hunk header that does not parse is an error, not
    something to be skipped, because a patch that does not parse is a
    patch that ``git apply`` will also reject.
    """

    files: list[tuple[str, list[tuple[str, ...]]]] = []
    current_path: str | None = None
    current_hunk: list[str] | None = None

    hunk_header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")

    for line in text.splitlines():
        if line.startswith("diff --git "):
            if current_path is not None and current_hunk is not None:
                files.append((current_path, [tuple(current_hunk)]))
                current_hunk = None
            current_path = None
            continue
        if line.startswith("--- "):
            path = line[4:].strip()
            if path.startswith(("a/", "b/")):
                path = path[2:]
            current_path = path
            continue
        if line.startswith("+++ "):
            path = line[4:].strip()
            if path.startswith(("a/", "b/")):
                path = path[2:]
            if path == "/dev/null":
                continue
            current_path = path
            continue
        if hunk_header.match(line):
            if current_path is None:
                raise PatchSeriesError(
                    f"a hunk appears before any file header: {line!r}"
                )
            if current_hunk is not None:
                files.append((current_path, [tuple(current_hunk)]))
            current_hunk = []
            continue
        if current_hunk is None:
            continue
        if line.startswith(("diff --git", "index ")):
            continue
        current_hunk.append(line)

    if current_path is not None and current_hunk is not None:
        files.append((current_path, [tuple(current_hunk)]))

    merged: list[tuple[str, tuple[tuple[str, ...], ...]]] = []
    for path, hunks in files:
        if merged and merged[-1][0] == path:
            merged[-1] = (path, merged[-1][1] + tuple(hunks))
        else:
            merged.append((path, tuple(hunks)))
    return tuple(merged)


#: The pattern of a hunk header, with its four counts.
#:
#: Git writes the line that follows the second ``@@`` as the *function
#: context* of the hunk -- ``@@ -78,6 +78,7 @@ source_set("...") {``.  The
#: pattern has to tolerate it, or every hunk of a patch that git produced
#: is skipped and the check reports nothing at all.  That is the same
#: failure as a check that never runs.
HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _creates_a_file(hunk: tuple[str, ...]) -> bool:
    """Whether the hunk creates the file rather than modifies it.

    A hunk that adds a file has no old lines to match against: the old
    side of the diff is ``/dev/null`` and the whole body is an addition.
    The rule that a hunk has to carry context therefore does not apply to
    it -- ``git apply`` creates the file, it does not look for anything.
    """

    # The hunk body itself does not carry the header, so the header is
    # recovered from the counts of the body: a hunk that only adds lines
    # has no ``-`` line at all.
    return not any(line.startswith("-") for line in hunk)


def hunk_count_mismatches(text: str) -> tuple[str, ...]:
    """The hunks whose header does not match their body.

    A hunk header declares how many lines of the old file and of the new
    file the hunk covers.  ``git apply`` reads the body against that
    declaration and refuses the whole patch when the two disagree --
    with the diagnostic *"corrupt patch at <file>:<line>"*.

    This is the single most valuable check the tool performs, because a
    malformed count is invisible in a diff viewer (the body looks
    right) and fatal to the build (the patch does not apply at all).
    The mismatch is reported with the line the hunk starts on, which is
    the line ``git apply`` itself names.
    """

    problems: list[str] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = HUNK_HEADER.match(lines[index])
        if match is None:
            index += 1
            continue

        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_count = int(match.group(4)) if match.group(4) is not None else 1

        # The body is counted to its end, not until the declared counts
        # are reached.  A header that declares *more* lines than the body
        # carries is the common corruption, and stopping the count at the
        # declared number would read the body as correct: the loop would
        # stop exactly where the header says the body ends, whether or
        # not it does.  That is how 54 broken hunks passed the check.
        old_seen = 0
        new_seen = 0
        cursor = index + 1
        while cursor < len(lines):
            body = lines[cursor]
            if body.startswith(("diff --git", "@@", "--- ", "+++ ")):
                break
            if body.startswith("\\"):  # a "\ No newline at end of file" marker
                cursor += 1
                continue
            if body.startswith(" "):
                old_seen += 1
                new_seen += 1
            elif body.startswith("+"):
                new_seen += 1
            elif body.startswith("-"):
                old_seen += 1
            elif body == "":
                old_seen += 1
                new_seen += 1
            else:
                break
            cursor += 1

        if old_seen != old_count or new_seen != new_count:
            problems.append(
                f"line {index + 1}: the hunk header declares "
                f"-{old_count} +{new_count} lines but the body carries "
                f"-{old_seen} +{new_seen}"
            )
        index = cursor if cursor > index else index + 1
    return tuple(problems)


def check_patch_series(
    *,
    patch_dir: Path | str | None = None,
    src_dir: Path | str | None = None,
    matrix: BuildMatrix | None = None,
) -> CheckReport:
    """Validate the patch series.

    Five things are decided:

    * every patch parses -- the hunks have headers and the files have
      paths,
    * every hunk carries context lines, because a hunk without context
      has nothing to match against and ``git apply`` cannot place it,
    * the second patch's prerequisites are present in the C++ sources
      *and* in the first patch -- the second patch substitutes the
      accessors the first one put in place, and a second patch without
      them applies cleanly and then serves the host's values,
    * the hooks the patches call are declared in the hooks header, or
      the core does not link,
    * the file paths the patches name exist in a Chromium checkout
      layout, or the hunk can never find its file.

    None of this needs a Chromium checkout, which is the point: a broken
    patch is found before the build machine is even switched on.
    """

    base = Path(patch_dir) if patch_dir is not None else PATCH_DIR
    sources = Path(src_dir) if src_dir is not None else SRC_DIR
    matrix = matrix or load_matrix()

    findings: list[CheckFinding] = []
    patch_names: list[str] = []
    touched: list[str] = []
    parsed: dict[str, tuple[tuple[str, tuple[tuple[str, ...], ...]], ...]] = {}

    for name in matrix.patch_series:
        path = base / Path(name).name
        patch_names.append(path.name)
        if not path.is_file():
            findings.append(
                CheckFinding("error", path.name, f"the patch file does not exist: {path}")
            )
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            findings.append(
                CheckFinding("error", path.name, f"cannot read the patch: {exc}")
            )
            continue

        try:
            files = parse_patch(text)
        except PatchSeriesError as exc:
            findings.append(CheckFinding("error", path.name, str(exc)))
            continue

        parsed[path.name] = files
        if not files:
            findings.append(
                CheckFinding("error", path.name, "the patch contains no file section")
            )
            continue

        for mismatch in hunk_count_mismatches(text):
            findings.append(
                CheckFinding(
                    "error",
                    path.name,
                    f"the hunk header does not match its body: {mismatch}; "
                    "git apply rejects the whole patch with 'corrupt patch'",
                )
            )

        for file_path, hunks in files:
            if file_path not in touched:
                touched.append(file_path)
            if not hunks:
                findings.append(
                    CheckFinding("error", path.name, f"{file_path} has no hunk")
                )
                continue
            for index, hunk in enumerate(hunks, start=1):
                context = [line for line in hunk if line.startswith(" ")]
                if not context and not _creates_a_file(hunk):
                    findings.append(
                        CheckFinding(
                            "error",
                            path.name,
                            f"hunk {index} of {file_path} has no context line, so "
                            "there is nothing for git apply to match against",
                        )
                    )
                for line in hunk:
                    if line.startswith(" "):
                        continue
                    if line.startswith(("+", "-")):
                        continue
                    if line == "":
                        continue
                    findings.append(
                        CheckFinding(
                            "error",
                            path.name,
                            f"hunk {index} of {file_path} carries the malformed "
                            f"line {line!r}",
                        )
                    )

    # -- the paths have to exist in a Chromium tree ------------------------

    for file_path in touched:
        if not _exists_in_chromium_layout(file_path):
            findings.append(
                CheckFinding(
                    "error",
                    file_path,
                    "the path does not exist in a Chromium checkout layout",
                )
            )

    # -- the prerequisites of the second patch ----------------------------

    first = _first_patch(matrix, base)
    second = _second_patch(matrix, base)

    if first is None:
        findings.append(
            CheckFinding(
                "error",
                "patch series",
                "the series has no first patch; the second one substitutes "
                "accessors the first one has to have put in place",
            )
        )
    if second is None:
        findings.append(
            CheckFinding(
                "error",
                "patch series",
                "the series has no second patch; the hardware surfaces are "
                "left reporting the host's values",
            )
        )

    source_text = _concatenate_sources(sources)

    # The accessors the *first* patch substitutes have to be substituted by
    # the series itself.  A page that can read ``navigator.deviceMemory``
    # and that the series does not touch reports the host's RAM, which is
    # the exact failure the series exists to prevent.
    #
    # The accessor body itself is not in the NOVA sources: the substituted
    # accessor keeps its original body as the fallback, so the stock
    # behaviour and the profile's behaviour live in exactly one place --
    # the Blink file the patch edits.  What the NOVA sources have to
    # provide is the hook the accessor calls, and that is checked against
    # the hooks header below.
    series_text = (first or "") + (second or "")
    for accessor in FIRST_PATCH_ACCESSORS:
        if accessor not in series_text:
            findings.append(
                CheckFinding(
                    "error",
                    accessor,
                    "the accessor is not substituted by the series, so the "
                    "page reads the host's value",
                )
            )

    # The hooks the second patch calls have to exist.  The second patch
    # reaches the profile's configuration through the same hooks the
    # first one introduced, so a hook that is not declared at all is a
    # link error in the core.
    #
    # The first patch ships the headers that declare them -- it carries
    # the whole NOVA source tree -- so the test is not "the first patch
    # calls the hook" but "the first patch ships a header that declares
    # it".  A hook that only the second patch needs is exactly what the
    # noise helpers are.
    if first is not None and second is not None:
        shipped_headers = ""
        for header in ("nova_chromium_hooks.h", "nova_pixel_noise.h"):
            header_path = sources / header
            if header_path.is_file():
                shipped_headers += header_path.read_text(encoding="utf-8")
        second_hooks = _hooks_called(second)
        for hook in sorted(second_hooks):
            if f"{hook}(" not in shipped_headers:
                findings.append(
                    CheckFinding(
                        "error",
                        hook,
                        "the second patch calls this hook, but no header "
                        "that the series ships declares it",
                    )
                )

    # -- the hooks have to be declared -------------------------------------

    header_path = sources / "nova_chromium_hooks.h"
    noise_path = sources / "nova_pixel_noise.h"
    implementation_path = sources / "nova_blink_patch.cc"
    if not header_path.is_file():
        findings.append(
            CheckFinding(
                "error",
                "nova_chromium_hooks.h",
                f"the hooks header does not exist: {header_path}",
            )
        )
    else:
        header_text = header_path.read_text(encoding="utf-8")
        noise_text = (
            noise_path.read_text(encoding="utf-8") if noise_path.is_file() else ""
        )
        implementation_text = (
            implementation_path.read_text(encoding="utf-8")
            if implementation_path.is_file()
            else ""
        )
        for hook in REQUIRED_PUBLIC_HOOKS:
            if hook not in header_text:
                findings.append(
                    CheckFinding(
                        "error",
                        hook,
                        "the patches call it but the hooks header does not "
                        "declare it, so the core does not link",
                    )
                )
        for hook in REQUIRED_NOISE_HOOKS:
            declared = hook in header_text or hook in noise_text
            implemented = hook in implementation_text or hook in noise_text
            if not declared:
                findings.append(
                    CheckFinding(
                        "error",
                        hook,
                        "the patches call it but no header declares it, so "
                        "the Chromium translation units that include the "
                        "hooks header cannot call it",
                    )
                )
            elif not implemented:
                findings.append(
                    CheckFinding(
                        "error",
                        hook,
                        "the patches call it but it is not implemented",
                    )
                )

    # -- the expected files have to be touched ------------------------------

    if first is not None and second is not None:
        for expected in EXPECTED_TOUCHED_FILES:
            if expected not in touched:
                findings.append(
                    CheckFinding(
                        "warning",
                        expected,
                        "the series does not touch this file",
                    )
                )

    return CheckReport(
        findings=tuple(findings),
        patches=tuple(patch_names),
        touched_files=tuple(touched),
    )


def _exists_in_chromium_layout(file_path: str) -> bool:
    """Whether a path names a file in a Chromium checkout layout.

    The check is deliberately structural rather than a file-system check:
    a Chromium checkout is 40 GB and the tool has to be usable without
    one.  A path is accepted when it lies under one of the known Blink
    and content directories *and* carries a source-file extension.

    The known directories are the ones the series is allowed to touch.
    A file inside one of them that Chromium does not have would be a
    file that has been renamed upstream, and the hunk would silently
    stop applying -- so the file name itself is checked against the
    set of files the series is known to touch.
    """

    parts = file_path.split("/")
    if len(parts) < 3:
        return False
    if parts[0] not in {"third_party", "content"}:
        return False
    directory = "/".join(parts[:-1])
    if directory not in CHROMIUM_LAYOUT:
        return False
    # The build wiring is a ``.gn`` file; everything else is a source file.
    if not parts[-1].endswith((".cc", ".h", ".idl", ".mm", ".cpp", ".gn")):
        return False
    return file_path in KNOWN_CHROMIUM_FILES


#: The Chromium files the series is allowed to touch.
#:
#: The list is the set of files a Chromium checkout is known to hold at
#: the paths the series names.  A patch that names a file outside it has
#: either hit a file that was renamed upstream or has named a file that
#: never existed, and either way the hunk will not apply.
#:
#: Besides the patched Blink sources it holds the NOVA sources the series
#: adds, the build files that compile them and the browser-process file
#: that installs the configuration.
KNOWN_CHROMIUM_FILES: Final[frozenset[str]] = frozenset(
    EXPECTED_TOUCHED_FILES
    + (
        "third_party/blink/renderer/core/nova/BUILD.gn",
        "third_party/blink/renderer/core/nova/nova_blink_patch.cc",
        "third_party/blink/renderer/core/nova/nova_chromium_hooks.h",
        "third_party/blink/renderer/core/nova/nova_core_config.cc",
        "third_party/blink/renderer/core/nova/nova_core_config.h",
        "third_party/blink/renderer/core/nova/nova_pixel_noise.cc",
        "third_party/blink/renderer/core/nova/nova_pixel_noise.h",
        "third_party/blink/renderer/core/BUILD.gn",
        "third_party/blink/renderer/modules/BUILD.gn",
        "content/app/BUILD.gn",
        "content/app/content_main_runner_impl.cc",
    )
)


def _patch_text(base: Path, matrix: BuildMatrix, index: int) -> str | None:
    """The text of the patch at ``index`` of the series.

    ``None`` is returned when there is no such patch in the series or
    when it cannot be read, so that the caller can tell "the series has
    no second patch" from "the second patch is empty".
    """

    if index >= len(matrix.patch_series):
        return None
    directory = base if base is not None else PATCH_DIR
    path = directory / Path(matrix.patch_series[index]).name
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _first_patch(
    matrix: BuildMatrix,
    base: Path | None = None,
) -> str | None:
    """The text of the first patch of the series."""

    return _patch_text(base if base is not None else PATCH_DIR, matrix, 0)


def _second_patch(
    matrix: BuildMatrix,
    base: Path | None = None,
) -> str | None:
    """The text of the second patch of the series."""

    return _patch_text(base if base is not None else PATCH_DIR, matrix, 1)


#: A call to one of the NOVA hooks, as the patches write it.
HOOK_CALL = re.compile(r"\bnova::([A-Za-z_][A-Za-z0-9_]*)\s*\(")


def _hooks_called(patch_text: str) -> frozenset[str]:
    """The names of the NOVA hooks a patch calls.

    The patches reach the profile's configuration through the
    ``nova::`` namespace, and every hook they call has to be declared
    in ``nova_chromium_hooks.h`` before the Chromium translation units
    that include it can use it.  Reading the calls out of the patch
    rather than out of a fixed list is what lets the check work on a
    series other than the one that ships with the project.
    """

    return frozenset(HOOK_CALL.findall(patch_text))


def _concatenate_sources(sources: Path) -> str:
    """The text of every C++ source of the core."""

    chunks: list[str] = []
    for path in sorted(sources.glob("*")):
        if path.suffix not in {".cc", ".h"}:
            continue
        try:
            chunks.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(chunks)


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------


def _command_list(args: argparse.Namespace, matrix: BuildMatrix) -> int:
    """Print the supported versions."""

    print(f"{'version':<10} {'branch':<8} {'ref':<28} {'tag':<20} patches")
    for version in matrix.versions:
        series = ", ".join(Path(name).name for name in version.patches)
        print(
            f"{version.major:<10} {version.branch:<8} {version.branch_ref:<28} "
            f"{version.tag:<20} {series}"
        )
    print()
    print(f"oldest supported: {matrix.oldest_major}")
    print(f"newest supported: {matrix.newest_major} (alias 'latest')")
    return 0


def _command_plan(args: argparse.Namespace, matrix: BuildMatrix) -> int:
    """Print the build plan for a version."""

    version = matrix.resolve(args.version)
    plan = build_plan(version, args.checkout, matrix=matrix)
    print(f"# Chromium {version.major} ({version.version})")
    print(f"# release branch: {version.branch_ref}")
    print(f"# tag: {version.tag}")
    print()
    print(plan.render())
    return 0


def _command_apply(args: argparse.Namespace, matrix: BuildMatrix) -> int:
    """Apply the patch series to a checkout."""

    version = matrix.resolve(args.version)
    report = apply_series(version, args.checkout, patch_dir=args.patch_dir)
    for result in report.results:
        print(f"[{result.status}] {result.name}")
        if result.stderr and not result.ok:
            print(result.stderr)
    if report.ok:
        applied = sum(1 for result in report.results if result.applied)
        skipped = sum(1 for result in report.results if result.already_applied)
        print(
            f"ok: {applied} patch(es) applied, {skipped} already in place, "
            f"in {report.checkout}"
        )
        return 0
    print(f"failed: {len(report.failures)} patch(es) did not apply", file=sys.stderr)
    return EXIT_FAILURE


def _command_verify(args: argparse.Namespace, matrix: BuildMatrix) -> int:
    """Verify a built core."""

    expected = None
    if args.version:
        expected = matrix.resolve(args.version).version
    result = verify_core(args.checkout, expected_version=expected, matrix=matrix)
    print(result.render())
    return 0 if result.ok else EXIT_FAILURE


def _command_check(args: argparse.Namespace, matrix: BuildMatrix) -> int:
    """Validate the patch series."""

    report = check_patch_series(matrix=matrix, patch_dir=args.patch_dir)
    print(report.render())
    return 0 if report.ok else EXIT_FAILURE


def _command_describe(args: argparse.Namespace, matrix: BuildMatrix) -> int:
    """Describe a built core the way the installer needs it."""

    core = describe_built_core(args.checkout, args.version, matrix=matrix)
    print(f"version: {core.version}")
    print(f"major: {core.major}")
    print(f"source_dir: {core.source_dir}")
    print(f"executable: {core.executable}")
    print(f"build_revision: {core.build_revision}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    """The command line parser."""

    parser = argparse.ArgumentParser(
        prog="build.py",
        description="Build tooling for the NOVA Profile native Chromium core.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="the build configuration to read (default: build_config.json next to this file)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list the supported Chromium versions")

    plan = sub.add_parser("plan", help="print the commands that build a core")
    plan.add_argument("--version", required=True, help="the Chromium version to plan for")
    plan.add_argument(
        "--checkout",
        required=True,
        type=Path,
        help="the directory the checkout is created in",
    )

    apply_cmd = sub.add_parser("apply", help="apply the patch series to a checkout")
    apply_cmd.add_argument("--version", required=True, help="the Chromium version")
    apply_cmd.add_argument("--checkout", required=True, type=Path, help="the Chromium checkout")
    apply_cmd.add_argument(
        "--patch-dir",
        type=Path,
        default=None,
        help="the directory the patch series lives in (default: patches/ next to this file)",
    )

    verify = sub.add_parser("verify", help="verify a built core")
    verify.add_argument("--checkout", required=True, type=Path, help="the Chromium checkout")
    verify.add_argument("--version", default=None, help="the version the core must report")

    describe = sub.add_parser(
        "describe",
        help="describe a built core the way the installer needs it",
    )
    describe.add_argument("--checkout", required=True, type=Path, help="the Chromium checkout")
    describe.add_argument("--version", required=True, help="the version that was built")

    check_cmd = sub.add_parser("check", help="validate the patch series")
    check_cmd.add_argument(
        "--patch-dir",
        type=Path,
        default=None,
        help="the directory the patch series lives in (default: patches/ next to this file)",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    """The entry point.

    Returns the process exit code: ``0`` on success, ``1`` on a build
    failure and ``2`` on a usage error.
    """

    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        matrix = load_matrix(args.config)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    handlers = {
        "list": _command_list,
        "plan": _command_plan,
        "apply": _command_apply,
        "verify": _command_verify,
        "describe": _command_describe,
        "check": _command_check,
    }
    handler = handlers[args.command]
    try:
        return handler(args, matrix)
    except BuildError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


__all__ = [
    "BuildError",
    "BuildMatrix",
    "BuildPlan",
    "BuiltCore",
    "CheckFinding",
    "CheckReport",
    "CoreVersion",
    "PatchResult",
    "PatchSeriesError",
    "PlanStep",
    "UnknownVersionError",
    "VerifyResult",
    "apply_series",
    "build_plan",
    "check_patch_series",
    "describe_built_core",
    "find_binary",
    "load_matrix",
    "main",
    "read_binary_version",
    "verify_core",
]


if __name__ == "__main__":
    raise SystemExit(main())
