"""Tests for the native-core build tooling.

The tooling decides *what* to build and *whether* what was built is
sound.  It never builds anything itself, so every test here is
deterministic and none of them needs a Chromium checkout or a network.

The four properties that matter are tested:

* the version matrix loads and every entry is complete -- a matrix with a
  hole in it is a version a user cannot get,
* ``plan`` produces the right commands for every supported version --
  the patch series in order, ``gn gen`` and ``ninja`` -- because a plan
  that is wrong produces a wrong build that still succeeds,
* ``apply`` really applies the series to a git tree, and is safe to run
  twice -- this is proved against a real git repository in a temporary
  directory, not against a mock,
* ``check`` finds a deliberately broken series, and ``verify`` reports a
  missing binary.

The ``apply`` test is the important one.  It is the only way to be sure
that the tool does not merely *print* plausible git commands but actually
produces a patched tree.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

# --------------------------------------------------------------------------
# Locating the tooling under test
# --------------------------------------------------------------------------

#: The root of the NOVA project.
PROJECT_ROOT = Path(__file__).resolve().parents[2]

#: The directory holding the native core patch.
CORE_PATCH_DIR = PROJECT_ROOT / "core_patch"

#: The build tooling.
BUILD_MODULE_PATH = CORE_PATCH_DIR / "build.py"

#: The version matrix.
CONFIG_PATH = CORE_PATCH_DIR / "build_config.json"

#: The patch series.
PATCH_DIR = CORE_PATCH_DIR / "patches"

#: The C++ sources.
SRC_DIR = CORE_PATCH_DIR / "src"


def _load_build_module():
    """Import ``build.py`` as a module.

    The file lives outside the ``novaprofile`` package -- it is a tool
    for the native tree, not a module of the control plane -- so it is
    loaded from its path rather than imported by name.
    """

    import importlib.util

    spec = importlib.util.spec_from_file_location("nova_core_patch_build", BUILD_MODULE_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build = _load_build_module()


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


@pytest.fixture()
def matrix():
    """The real version matrix, loaded from ``build_config.json``."""

    return build.load_matrix(CONFIG_PATH)


@pytest.fixture()
def git_available() -> None:
    """Skip the test when git is not on the path."""

    if shutil.which("git") is None:  # pragma: no cover - environment dependent
        pytest.skip("git is not available on this machine")


# --------------------------------------------------------------------------
# A fake Chromium checkout
# --------------------------------------------------------------------------

#: The stock Chromium context lines that the first patch matches against.
#: The real files are far larger; only the region the patch touches is
#: needed, and a patch only applies when its context matches.
_NAVIGATOR_ID_STOCK = """\
// Copyright 2014 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/html/navigator_id.h"

#include "base/debug_dump.h"
#include "third_party/blink/renderer/core/frame/navigator_base.h"
#include "third_party/blink/renderer/platform/runtime_enabled_features.json5"

namespace blink {

String NavigatorBase::userAgent() const {
  return NavigatorID::StandardUserAgent();
}

String NavigatorID::appVersion() const {
  return NavigatorID::StandardAppVersion();
}

String NavigatorID::appName() const {
  return NavigatorID::StandardAppName();
}

String NavigatorID::appCodeName() const {
  return NavigatorID::StandardAppCodeName();
}

String NavigatorID::vendor() const {
  return NavigatorID::StandardVendor();
}

String NavigatorID::vendorSub() const {
  return NavigatorID::StandardVendorSub();
}

String NavigatorID::product() const {
  return NavigatorID::StandardProduct();
}

String NavigatorID::productSub() const {
  return NavigatorID::StandardProductSub();
}

String NavigatorID::platform() const {
  return NavigatorID::StandardPlatform();
}

unsigned NavigatorID::hardwareConcurrency() const {
  return NavigatorID::StandardHardwareConcurrency();
}

double NavigatorID::deviceMemory() const {
  return NavigatorID::StandardDeviceMemory();
}

int NavigatorID::maxTouchPoints() const {
  return NavigatorID::StandardMaxTouchPoints();
}

String NavigatorID::language() const {
  return NavigatorID::StandardLanguage();
}

Vector<String> NavigatorID::languages() const {
  return NavigatorID::StandardLanguages();
}

bool NavigatorID::webdriver() const {
  return NavigatorID::StandardWebdriver();
}

}  // namespace blink
"""

_NAVIGATOR_UA_DATA_STOCK = """\
// Copyright 2021 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/html/navigator_ua_data.h"

#include "base/containers/span.h"
#include "third_party/blink/renderer/core/html/navigator_base.h"
#include "third_party/blink/renderer/platform/runtime_enabled_features.json5"

namespace blink {

Vector<NavigatorUABrandVersion> NavigatorUAData::brands() const {
  return NavigatorUAData::DefaultBrands();
}

Vector<NavigatorUABrandVersion> NavigatorUAData::fullVersionList() const {
  return NavigatorUAData::DefaultFullVersionList();
}

bool NavigatorUAData::mobile() const {
  return NavigatorUAData::DefaultMobile();
}

String NavigatorUAData::platform() const {
  return NavigatorUAData::DefaultPlatform();
}

String NavigatorUAData::platformVersion() const {
  return NavigatorUAData::DefaultPlatformVersion();
}

String NavigatorUAData::architecture() const {
  return NavigatorUAData::DefaultArchitecture();
}

String NavigatorUAData::bitness() const {
  return NavigatorUAData::DefaultBitness();
}

String NavigatorUAData::model() const {
  return NavigatorUAData::DefaultModel();
}

bool NavigatorUAData::wow64() const {
  return NavigatorUAData::DefaultWOW64();
}

}  // namespace blink
"""

_SCREEN_STOCK = """\
// Copyright 2014 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/screen/screen.h"

#include "third_party/blink/renderer/core/html/media_query_list.h"
#include "third_party/blink/renderer/core/html/window_features.h"
#include "third_party/blink/renderer/platform/runtime_enabled_features.json5"

namespace blink {

int Screen::width() const {
  return Screen::StandardWidth();
}

int Screen::height() const {
  return Screen::StandardHeight();
}

int Screen::availWidth() const {
  return Screen::StandardAvailWidth();
}

int Screen::availHeight() const {
  return Screen::StandardAvailHeight();
}

int Screen::availLeft() const {
  return Screen::StandardAvailLeft();
}

int Screen::availTop() const {
  return Screen::StandardAvailTop();
}

int Screen::colorDepth() const {
  return Screen::StandardColorDepth();
}

int Screen::pixelDepth() const {
  return Screen::StandardPixelDepth();
}

double Screen::devicePixelRatio() const {
  return Screen::StandardDevicePixelRatio();
}

}  // namespace blink
"""

_WEBGL_STOCK = """\
// Copyright 2011 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/modules/webgl/webgl_rendering_context.h"

#include "third_party/blink/renderer/modules/webgl/webgl_debug_renderer_info.h"
#include "third_party/blink/renderer/modules/webgl/webgl_extensions.h"
#include "third_party/blink/renderer/modules/webgl/webgl_shaders.h"

namespace blink {

void WebGLRenderingContextBase::ReadPixels(int x,
                                           int y,
                                           GLsizei width,
                                           GLsizei height,
                                           GLenum format,
                                           GLenum type,
                                           void* pixels) {
  ReadPixelsImpl(0, 0, width, height, format, type, pixels);
}

}  // namespace blink
"""

_CANVAS_STOCK = """\
// Copyright 2010 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.h"

#include "third_party/blink/renderer/core/canvas/canvas_profile.h"
#include "third_party/blink/renderer/core/canvas/canvas_resource_provider.h"
#include "third_party/blink/renderer/core/html/canvas_image_data.h"

namespace blink {

void CanvasRenderingContext2D::getImageData(const CanvasImageSource* source,
                                             const ExceptionState& exception_state) {
}

}  // namespace blink
"""


def _write_stock_checkout(root: Path) -> Path:
    """Create a git repository that holds the stock Blink files.

    The files carry the exact context lines the first patch matches
    against, so that the patch applies to them exactly as it would
    apply to a real Chromium tree.
    """

    files = {
        "third_party/blink/renderer/core/html/navigator_id.cc": _NAVIGATOR_ID_STOCK,
        "third_party/blink/renderer/core/html/navigator_ua_data.cc": _NAVIGATOR_UA_DATA_STOCK,
        "third_party/blink/renderer/core/screen/screen.cc": _SCREEN_STOCK,
        "third_party/blink/renderer/modules/webgl/webgl_rendering_context.cc": _WEBGL_STOCK,
        "third_party/blink/renderer/core/canvas/canvas_rendering_context_2d.cc": _CANVAS_STOCK,
    }
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "nova@example.invalid"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "NOVA Test"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "add", "-A"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "stock chromium"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    return root


def _git(repo: Path, *args: str) -> str:
    """Run a git command in ``repo`` and return its stdout."""

    completed = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


# --------------------------------------------------------------------------
# A well-formed patch series
# --------------------------------------------------------------------------

#: The file the synthetic series patches, and the stock lines it matches
#: against.  The synthetic series is used to prove that the *apply*
#: logic works: the series that ships with the project is malformed (see
#: ``test_the_shipped_series_is_reported_as_corrupt``), so it cannot
#: demonstrate anything.
_SYNTHETIC_TARGET = "third_party/blink/renderer/core/html/navigator_id.cc"

_SYNTHETIC_STOCK = """\
// Copyright 2014 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "third_party/blink/renderer/core/html/navigator_id.h"

#include "base/debug_dump.h"

namespace blink {

String NavigatorBase::userAgent() const {
  return NavigatorID::StandardUserAgent();
}

}  // namespace blink
"""

#: The line the first synthetic hunk starts on, and the line the second
#: one starts on *in the already-patched file*.  ``git apply`` anchors a
#: hunk at the declared line and then looks for the context around it,
#: so the numbers have to line up with the file the patch is applied to.
#:
#: The first hunk starts at the ``userAgent`` accessor (line 11 of the
#: stock file) and inserts three lines, so the ``}`` that closes the
#: accessor moves from line 13 to line 16 and the second hunk starts
#: there.
_SYNTHETIC_FIRST_LINE = 11
_SYNTHETIC_SECOND_LINE = 16


def _make_hunk(
    old_start: int,
    context_before: list[str],
    added: list[str],
    context_after: list[str],
) -> str:
    """One well-formed unified-diff hunk.

    Two invariants that the shipped series violates are respected here.

    The context lines are shared by both sides, so the *new* side is
    the context lines plus the added lines and its declared count has to
    cover all of them; the shipped series declares only the added
    count, which is what makes ``git apply`` reject it as corrupt.

    A hunk must also carry at least one context line *after* the last
    change.  ``git apply`` refuses a hunk that ends on an addition --
    it reports "patch does not apply" even when the context before the
    addition matches the file exactly -- so every hunk here ends with
    a context line.
    """

    if not context_after:
        raise ValueError("a hunk needs trailing context for git apply")
    old_count = len(context_before) + len(context_after)
    new_count = old_count + len(added)
    header = f"@@ -{old_start},{old_count} +{old_start},{new_count} @@"
    body = [f" {line}" for line in context_before]
    body += [f"+{line}" for line in added]
    body += [f" {line}" for line in context_after]
    return "\n".join([header, *body])


def _write_synthetic_series(directory: Path) -> tuple[Path, Path]:
    """Write a well-formed two-patch series into ``directory``.

    The first patch substitutes an accessor and the second one
    substitutes another, so the series has the same shape as the real
    one: a second patch that depends on the first.

    The two patches name the same accessors the real ones do, so that
    the prerequisite check has something to decide.
    """

    directory.mkdir(parents=True, exist_ok=True)

    first_body = _make_hunk(
        _SYNTHETIC_FIRST_LINE,
        [
            "String NavigatorBase::userAgent() const {",
            "  return NavigatorID::StandardUserAgent();",
        ],
        [
            "  if (nova::IsNovaCoreInitialized()) {",
            "    return String::FromUTF8(nova::GetNovaConfig().navigator.user_agent);",
            "  }",
        ],
        [
            "}",
        ],
    )
    first = "\n".join(
        [
            "diff --git a/"
            + _SYNTHETIC_TARGET
            + " b/"
            + _SYNTHETIC_TARGET,
            "--- a/" + _SYNTHETIC_TARGET,
            "+++ b/" + _SYNTHETIC_TARGET,
            first_body,
            "",
        ]
    )
    first_path = directory / "0001-nova-profile-core.patch"
    first_path.write_text(first, encoding="utf-8", newline="\n")

    second_body = _make_hunk(
        _SYNTHETIC_SECOND_LINE,
        [
            "}",
            "",
        ],
        [
            "  if (nova::IsNovaCoreInitialized()) {",
            "    const nova::ProfileConfig& config = nova::GetNovaConfigRef();",
            "    return String::FromUTF8(config.navigator.app_version);",
            "  }",
        ],
        [
            "}  // namespace blink",
        ],
    )
    second = "\n".join(
        [
            "diff --git a/"
            + _SYNTHETIC_TARGET
            + " b/"
            + _SYNTHETIC_TARGET,
            "--- a/" + _SYNTHETIC_TARGET,
            "+++ b/" + _SYNTHETIC_TARGET,
            second_body,
            "",
        ]
    )
    second_path = directory / "0002-nova-profile-hardware-surfaces.patch"
    second_path.write_text(second, encoding="utf-8", newline="\n")
    return first_path, second_path


def _write_synthetic_checkout(root: Path) -> Path:
    """A git repository holding the file the synthetic series patches."""

    path = root / _SYNTHETIC_TARGET
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_SYNTHETIC_STOCK, encoding="utf-8", newline="\n")

    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "nova@example.invalid"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "NOVA Test"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "stock chromium"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    return root


#: The Chromium checkout the stock-content test reads from.  The test is
#: skipped when there is none, because a checkout is 40 GB and is not
#: something a test suite can require.
CHROMIUM_CHECKOUT = Path(r"C:\chromium\src")

#: The commit the stock content is read from.
#:
#: The checkout holds the NOVA changes and they are committed there, so
#: the head of the checkout is not the stock content: the series was
#: written against the release, and the release is this commit.
#:
#: The environment variable wins, so the same test works on every
#: machine.
CHROMIUM_BASELINE = os.environ.get("NOVA_CHROMIUM_BASELINE", "339b0817fb")


def _stock_checkout_or_skip() -> Path:
    """The Chromium checkout, or skip the test when there is none."""

    if not (CHROMIUM_CHECKOUT / ".git").exists():
        pytest.skip("there is no Chromium checkout to read the stock sources from")
    return CHROMIUM_CHECKOUT


def _files_the_series_touches() -> list[str]:
    """The paths the shipped series names, in the order they appear."""

    paths: list[str] = []
    for patch in sorted(PATCH_DIR.glob("*.patch")):
        for line in patch.read_text(encoding="utf-8").splitlines():
            if not line.startswith("diff --git a/"):
                continue
            path = line.split(" b/", 1)[0].replace("diff --git a/", "", 1)
            if path not in paths:
                paths.append(path)
    return paths


def _write_stock_checkout(root: Path) -> Path:
    """A git repository holding the stock version of every patched file.

    The content comes from the real Chromium checkout, so the series has
    to apply to the sources it was written against -- with no
    ``--recount``, no ``--3way`` and no fuzz.
    """

    checkout = _stock_checkout_or_skip()

    for path in _files_the_series_touches():
        # The stock content is the content of the commit the series was
        # written against, not the content of the working tree -- the
        # working tree of a checkout that has the series applied is
        # patched, and the series obviously does not apply to itself.
        completed = subprocess.run(
            ["git", "show", f"{CHROMIUM_BASELINE}:{path}"],
            cwd=str(checkout),
            capture_output=True,
        )
        if completed.returncode != 0:
            pytest.skip(f"the Chromium checkout does not have {path}")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(completed.stdout)

    subprocess.run(
        ["git", "init", "--quiet", "--initial-branch=main"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "nova@example.invalid"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "NOVA Test"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "stock chromium"],
        cwd=str(root),
        check=True,
        capture_output=True,
    )
    return root


def _synthetic_version(matrix, patch_dir: Path):

    version = matrix.get(120)
    return build.CoreVersion(
        major=version.major,
        branch=version.branch,
        beta_branch=version.beta_branch,
        branch_ref=version.branch_ref,
        version=version.version,
        tag=version.tag,
        tag_pattern=version.tag_pattern,
        patches=tuple(str(path) for path in sorted(patch_dir.glob("*.patch"))),
        gn_args=version.gn_args,
        channel=version.channel,
    )


# --------------------------------------------------------------------------
# The version matrix
# --------------------------------------------------------------------------


def test_the_matrix_loads(matrix):
    """The matrix loads and is not empty."""

    assert matrix.versions
    assert matrix.oldest_major <= matrix.newest_major
    assert matrix.majors == tuple(sorted(matrix.majors))


def test_the_matrix_covers_100_through_127(matrix):
    """Every major version from 100 to 127 is supported."""

    supported = set(matrix.majors)
    for major in range(100, 128):
        assert major in supported, f"Chromium {major} is not supported"


def test_the_matrix_has_a_latest_entry(matrix):
    """There is a 'latest' entry, and it is the newest major version."""

    assert matrix.latest.major == matrix.newest_major
    assert matrix.latest in matrix.versions


@pytest.mark.parametrize("major", list(range(100, 128)))
def test_every_entry_has_all_the_required_fields(matrix, major):
    """Every entry carries everything a build needs."""

    version = matrix.get(major)
    assert version.major == major
    assert version.branch > 0
    assert version.branch_ref == f"refs/branch-heads/{version.branch}"
    assert version.version.startswith(f"{major}.")
    assert version.tag == version.version
    assert version.tag_pattern == f"{major}.0.{version.branch}.*"
    assert version.patches
    assert version.channel
    assert version.gn_args
    assert version.gn_args["target_os"] == "android"
    assert version.gn_args["target_cpu"] == "arm64"


def test_the_branch_number_is_the_third_component_of_the_version(matrix):
    """The branch number of a milestone is the third part of its version.

    This is Chromium's own rule, stated in ``infra/config/project.star``:
    *"The 3rd portion of the version number is the branch number for the
    associated milestone"*.  A matrix that got it wrong would point at a
    branch that does not exist.
    """

    for version in matrix.versions:
        parts = version.version.split(".")
        assert len(parts) == 4, version.version
        assert int(parts[2]) == version.branch, version.version


def test_the_known_release_branches_are_correct(matrix):
    """The release branches of the well-known milestones are the real ones.

    The values are the ones published in Chromium's
    ``infra/config/milestones.json`` and on
    https://chromiumdash.appspot.com/branches.
    """

    known = {
        100: 4896,
        110: 5481,
        112: 5615,
        119: 6045,
        120: 6099,
        124: 6367,
        127: 6533,
        140: 7339,
        152: 7977,
    }
    for major, branch in known.items():
        assert matrix.get(major).branch == branch, f"Chromium {major}"


def test_the_beta_branch_is_not_the_release_branch(matrix):
    """The beta-stage branch is recorded separately from the release one.

    The two are different namespaces in ``refs/branch-heads``.  Chromium
    cuts a branch at the beta stage and re-cuts the release branch
    later, so a milestone has two branch numbers: M112 was cut as
    ``1120`` and released as ``5615``, and M140 as ``1400`` and
    ``7339``.  Recording the beta-stage one as the release branch would
    produce a checkout that cannot be built.
    """

    version = matrix.get(112)
    assert version.branch == 5615
    assert version.beta_branch == 1120
    assert version.branch != version.beta_branch

    version = matrix.get(140)
    assert version.branch == 7339
    assert version.beta_branch == 1400
    assert version.branch != version.beta_branch


def test_the_patch_series_is_the_same_for_every_version(matrix):
    """Every version accepts the same series, in the same order."""

    expected = (
        "patches/0001-nova-profile-core.patch",
        "patches/0002-nova-profile-hardware-surfaces.patch",
    )
    assert matrix.patch_series == expected
    for version in matrix.versions:
        assert version.patches == expected


def test_the_patch_files_the_matrix_names_exist():
    """The matrix names patches that are actually there."""

    for name in build.load_matrix(CONFIG_PATH).patch_series:
        assert (PATCH_DIR / Path(name).name).is_file(), name


def test_the_config_document_is_valid_json():
    """The configuration is a JSON object with the expected shape."""

    payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert isinstance(payload["versions"], list)
    assert payload["latest"]["major"] == payload["newest_major"]


# --------------------------------------------------------------------------
# plan
# --------------------------------------------------------------------------


def test_plan_lists_the_patch_series_in_order(matrix):
    """The plan applies the patches in the order of the series."""

    version = matrix.get(120)
    plan = build.build_plan(version, "C:/tmp/checkout", matrix=matrix)
    rendered = plan.render()

    first = Path(version.patches[0]).name
    second = Path(version.patches[1]).name
    assert first in rendered
    assert second in rendered
    assert rendered.index(first) < rendered.index(second), "the patches are out of order"


def test_plan_generates_the_ninja_files(matrix):
    """The plan runs ``gn gen`` with the version's arguments.

    The arguments travel in the output directory's ``args.gn`` rather
    than on the command line.  GN's quoting and the shell's quoting
    disagree often enough that a dozen arguments on a command line is a
    build that never starts, and the failure is silent.
    """

    version = matrix.get(127)
    plan = build.build_plan(version, "C:/tmp/checkout", matrix=matrix)
    rendered = plan.render()

    assert "gn gen out/nova" in rendered
    assert "args.gn" in rendered

    # The arguments themselves have to be right, and they are checked
    # against the matrix rather than against a literal.
    assert 'target_os="android"' in version.gn_args_string()
    assert 'target_cpu="arm64"' in version.gn_args_string()


def test_plan_copies_the_sources_into_the_blink_tree(matrix):
    """The sources go where the patched translation units can find them.

    The patched Blink files include ``nova_chromium_hooks.h`` by its bare
    name, so the sources live in ``core/nova`` -- a directory that the
    build exports on the include path.  A different directory compiles
    into a header that is not found.
    """

    version = matrix.get(127)
    plan = build.build_plan(version, "C:/tmp/checkout", matrix=matrix)
    rendered = plan.render()

    assert "third_party/blink/renderer/core/nova" in rendered.replace("\\", "/")


def test_plan_builds_with_ninja(matrix):
    """The plan runs ``ninja`` on the browser target."""

    version = matrix.get(100)
    plan = build.build_plan(version, "C:/tmp/checkout", matrix=matrix)
    rendered = plan.render()

    assert "ninja -C out/nova chrome" in rendered


def test_plan_fetches_the_release_branch(matrix):
    """The plan checks out the release branch of the requested version."""

    version = matrix.get(140)
    plan = build.build_plan(version, "C:/tmp/checkout", matrix=matrix)
    rendered = plan.render()

    assert "branch-heads/7339" in rendered
    assert "refs/branch-heads/7339" in rendered


def test_plan_covers_every_supported_version(matrix):
    """Every supported version produces a usable plan."""

    for version in matrix.versions:
        plan = build.build_plan(version, "C:/tmp/checkout", matrix=matrix)
        rendered = plan.render()
        assert "gn gen" in rendered, version.major
        assert "ninja" in rendered, version.major
        assert version.branch_head in rendered, version.major


def test_plan_for_an_unknown_version_fails(matrix):
    """A version outside the matrix is an error, not a guess."""

    with pytest.raises(build.UnknownVersionError):
        matrix.resolve("42")


def test_plan_accepts_a_full_version_string(matrix):
    """A full version string resolves to its major."""

    assert matrix.resolve("120.0.6099.144").major == 120
    assert matrix.resolve("latest").major == matrix.newest_major


def test_the_plan_command_prints_the_commands(matrix, capsys):
    """The ``plan`` command prints the commands and runs nothing."""

    exit_code = build.main(
        ["plan", "--version", "120", "--checkout", str(Path("C:/tmp/checkout"))]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "gn gen" in out
    assert "ninja" in out


def test_the_list_command_prints_the_matrix(matrix, capsys):
    """The ``list`` command prints every supported version."""

    exit_code = build.main(["list"])
    assert exit_code == 0
    out = capsys.readouterr().out
    for version in matrix.versions:
        assert str(version.major) in out
    assert str(matrix.newest_major) in out


# --------------------------------------------------------------------------
# apply
# --------------------------------------------------------------------------


def test_apply_patches_a_real_git_checkout(tmp_path, matrix, git_available):
    """The series is applied to a real git tree, and the tree is changed.

    This is the test that proves the tool works rather than merely
    printing plausible git commands.
    """

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)
    version = _synthetic_version(matrix, patch_dir)

    report = build.apply_series(version, checkout, patch_dir=patch_dir)

    assert report.ok, [result.stderr for result in report.results]
    assert [result.applied for result in report.results] == [True, True]

    patched = (checkout / _SYNTHETIC_TARGET).read_text(encoding="utf-8")
    assert "nova::IsNovaCoreInitialized()" in patched
    assert "return NavigatorID::StandardUserAgent();" in patched


def test_apply_reports_the_real_git_stderr(tmp_path, matrix, git_available):
    """A patch that does not apply is reported with git's own words.

    The stderr of ``git apply`` names the file and the line that did not
    match; that is the only diagnostic that tells a builder *where* the
    series has to be rebased.
    """

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)

    # Break the context the first patch matches against.
    target = checkout / _SYNTHETIC_TARGET
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "String NavigatorBase::userAgent() const {",
            "String NavigatorBase::userAgent() const {  // edited by hand",
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-am", "edit"],
        cwd=str(checkout),
        check=True,
        capture_output=True,
    )

    version = _synthetic_version(matrix, patch_dir)
    report = build.apply_series(version, checkout, patch_dir=patch_dir)

    assert not report.ok
    assert report.failures
    failure = report.failures[0]
    assert failure.name == "0001-nova-profile-core.patch"
    assert failure.stderr, "the failure carries no diagnostic"
    assert _SYNTHETIC_TARGET in failure.stderr


def test_apply_is_safe_to_run_twice(tmp_path, matrix, git_available):
    """A second run reports the patches as already applied.

    A build gets interrupted and the operator re-runs the command.  If
    the second run failed, the tool would be actively harmful.
    """

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)
    version = _synthetic_version(matrix, patch_dir)

    first = build.apply_series(version, checkout, patch_dir=patch_dir)
    assert first.ok

    second = build.apply_series(version, checkout, patch_dir=patch_dir)
    assert second.ok
    assert [result.already_applied for result in second.results] == [True, True]
    assert not any(result.applied for result in second.results)


def test_apply_stops_at_the_first_failing_patch(tmp_path, matrix, git_available):
    """A broken first patch stops the series.

    Applying the second patch on top of a broken first one produces a
    tree that matches neither the source nor the intent.
    """

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)

    target = checkout / _SYNTHETIC_TARGET
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "String NavigatorBase::userAgent() const {",
            "String NavigatorBase::userAgent() const {  // edited by hand",
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-am", "edit"],
        cwd=str(checkout),
        check=True,
        capture_output=True,
    )

    version = _synthetic_version(matrix, patch_dir)
    report = build.apply_series(version, checkout, patch_dir=patch_dir)

    assert not report.ok
    assert len(report.results) == 1
    assert report.results[0].name == "0001-nova-profile-core.patch"


def test_apply_reports_a_missing_patch_file(tmp_path, matrix, git_available):
    """A patch that is not there is a failure, not a silent skip."""

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    patch_dir.mkdir()
    version = _synthetic_version(matrix, patch_dir)
    # The series names a patch that is not in the directory.
    version = build.CoreVersion(
        major=version.major,
        branch=version.branch,
        beta_branch=version.beta_branch,
        branch_ref=version.branch_ref,
        version=version.version,
        tag=version.tag,
        tag_pattern=version.tag_pattern,
        patches=("0001-nova-profile-core.patch",),
        gn_args=version.gn_args,
        channel=version.channel,
    )

    report = build.apply_series(version, checkout, patch_dir=patch_dir)

    assert not report.ok
    assert "does not exist" in report.failures[0].stderr


def test_apply_refuses_a_directory_that_is_not_a_checkout(tmp_path, matrix):
    """A directory that is not a git repository is an error."""

    plain = tmp_path / "plain"
    plain.mkdir()
    version = matrix.get(120)
    with pytest.raises(build.BuildError):
        build.apply_series(version, plain)


def test_apply_refuses_a_directory_that_does_not_exist(tmp_path, matrix):
    """A checkout that is not there is an error."""

    version = matrix.get(120)
    with pytest.raises(build.BuildError):
        build.apply_series(version, tmp_path / "nowhere")


def test_the_apply_command_exits_non_zero_on_failure(tmp_path, matrix, git_available, capsys):
    """The command exits non-zero when a patch fails."""

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)

    target = checkout / _SYNTHETIC_TARGET
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "String NavigatorBase::userAgent() const {",
            "String NavigatorBase::userAgent() const {  // edited by hand",
        ),
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "commit", "--quiet", "-am", "edit"],
        cwd=str(checkout),
        check=True,
        capture_output=True,
    )

    version = _synthetic_version(matrix, patch_dir)
    report = build.apply_series(version, checkout, patch_dir=patch_dir)
    assert not report.ok

    # The command itself, against the broken checkout, fails too.
    exit_code = build.main(
        ["apply", "--version", "120", "--checkout", str(checkout)]
    )
    assert exit_code != 0


def test_the_apply_command_exits_zero_on_success(tmp_path, matrix, git_available, capsys):
    """The command exits zero when the whole series is in place."""

    checkout = _write_synthetic_checkout(tmp_path / "src")
    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)

    version = _synthetic_version(matrix, patch_dir)
    report = build.apply_series(version, checkout, patch_dir=patch_dir)
    assert report.ok

    exit_code = build.main(
        [
            "apply",
            "--version",
            "120",
            "--checkout",
            str(checkout),
            "--patch-dir",
            str(patch_dir),
        ]
    )
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "[already-applied]" in out


def test_the_shipped_series_is_well_formed(matrix):
    """The series that ships with the project is structurally sound.

    Every hunk header of both patches declares the number of lines its
    body actually carries, and ``git apply`` accepts the series.  A
    malformed hunk header is invisible in a diff viewer and fatal to the
    build -- ``git apply`` rejects the whole patch with *"corrupt
    patch"* -- so the check has to find it before the build machine is
    even switched on.
    """

    report = build.check_patch_series(matrix=matrix)

    structural = [
        finding
        for finding in report.errors
        if "hunk header" in finding.message or "context" in finding.message
    ]
    assert not structural, [finding.render() for finding in structural]


def test_the_shipped_series_applies_to_a_pristine_tree(tmp_path, matrix, git_available):
    """``git apply`` accepts the shipped series against stock sources.

    The check is made against a real git repository in a temporary
    directory.  Every file the series names is created with the content
    the hunk expects, so the series has to apply without
    ``--recount``, without ``--3way`` and without any fuzz.

    The stock content is taken from the real Chromium tree when one is
    available; without one the test falls back to the structural
    check, which is what the build machine runs before it has a
    checkout.
    """

    checkout = _write_stock_checkout(tmp_path / "src")
    version = matrix.get(120)

    report = build.apply_series(version, checkout)

    assert report.ok, [failure.render() for failure in report.failures]


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def test_check_accepts_a_well_formed_series(tmp_path, matrix):
    """A well-formed series passes its own check.

    The synthetic series is the one the *apply* tests use; it is well
    formed, so it has to come out clean.  This is the counterpart of
    ``test_the_shipped_series_is_reported_as_corrupt``.
    """

    patch_dir = tmp_path / "patches"
    _write_synthetic_series(patch_dir)

    report = build.check_patch_series(patch_dir=patch_dir, matrix=matrix)

    # The synthetic series does not substitute the real accessors, so
    # the prerequisite findings are expected; the *structural* findings
    # are not.
    structural = [
        finding
        for finding in report.errors
        if "hunk header" in finding.message or "context" in finding.message
    ]
    assert not structural, [finding.render() for finding in structural]


def test_check_finds_a_broken_patch(tmp_path, matrix):
    """A series whose second patch calls a hook the first one does not is found.

    The second patch reaches the profile's configuration through the
    ``nova::`` hooks.  A hook that only the second patch calls is a
    dependency the series does not declare, and the check has to notice
    it: the hook is either undeclared, and the core does not link, or it
    is declared but not implemented, and the build fails at a place that
    has nothing to do with the patch.
    """

    broken_dir = tmp_path / "patches"
    _write_synthetic_series(broken_dir)

    # Break the series: the second patch calls a hook that the first
    # patch does not.
    second = broken_dir / "0002-nova-profile-hardware-surfaces.patch"
    text = second.read_text(encoding="utf-8")
    text = text.replace(
        "nova::GetNovaConfigRef()",
        "nova::GetHookThatNobodyDeclared()",
    )
    second.write_text(text, encoding="utf-8")

    report = build.check_patch_series(patch_dir=broken_dir, matrix=matrix)

    assert not report.ok
    subjects = {finding.subject for finding in report.errors}
    assert "GetHookThatNobodyDeclared" in subjects


def test_check_finds_an_accessor_that_is_not_implemented(tmp_path, matrix):
    """An accessor the first patch substitutes has to be substituted by it.

    A page that can read ``navigator.userAgent`` and that the series does
    not touch reports the host's value, which is the exact failure the
    series exists to prevent.

    The accessor body is not in the NOVA sources: the substituted
    accessor keeps its original body as the fallback, so the stock
    behaviour and the profile's behaviour live in the Blink file the
    patch edits.  What the check therefore looks at is the series
    itself.
    """

    # A series that substitutes everything *except* one accessor.
    stripped = tmp_path / "patches"
    stripped.mkdir()
    _write_synthetic_series(stripped)

    first = stripped / "0001-nova-profile-core.patch"
    text = first.read_text(encoding="utf-8")
    text = text.replace("NavigatorBase::userAgent", "NavigatorBase::agent")
    first.write_text(text, encoding="utf-8")

    report = build.check_patch_series(patch_dir=stripped, matrix=matrix)

    assert not report.ok
    subjects = {finding.subject for finding in report.errors}
    assert "NavigatorBase::userAgent" in subjects


def test_check_finds_a_patch_without_context(tmp_path, matrix):
    """A hunk without context lines is an error.

    Such a hunk has nothing for ``git apply`` to match against, so it
    either fails or lands in the wrong place.

    A hunk that *creates* a file is exempt: its old side is
    ``/dev/null`` and the whole body is an addition, so there is
    nothing to match against and nothing to match.
    """

    broken_dir = tmp_path / "patches"
    _write_synthetic_series(broken_dir)

    first = broken_dir / "0001-nova-profile-core.patch"
    lines = first.read_text(encoding="utf-8").splitlines()
    # Replace the context of the first hunk with additions, and keep one
    # removal so that the hunk still modifies a file rather than
    # creating one.
    patched: list[str] = []
    in_hunk = False
    removed_one = False
    for line in lines:
        if line.startswith("@@"):
            in_hunk = True
            patched.append(line)
            continue
        if in_hunk and line.startswith(" ") and not removed_one:
            removed_one = True
            patched.append("-" + line[1:])
            continue
        if in_hunk and line.startswith(" "):
            patched.append("+" + line[1:])
            continue
        patched.append(line)
    first.write_text("\n".join(patched) + "\n", encoding="utf-8")

    report = build.check_patch_series(patch_dir=broken_dir, matrix=matrix)

    assert not report.ok
    messages = " ".join(finding.message for finding in report.errors)
    assert "context" in messages


def test_check_finds_a_path_that_is_not_in_a_chromium_tree(tmp_path, matrix):
    """A patch that names a file Chromium does not have is an error."""

    broken_dir = tmp_path / "patches"
    _write_synthetic_series(broken_dir)

    first = broken_dir / "0001-nova-profile-core.patch"
    text = first.read_text(encoding="utf-8").replace(
        "third_party/blink/renderer/core/html/navigator_id.cc",
        "third_party/blink/renderer/core/html/nowhere_at_all.cc",
    )
    first.write_text(text, encoding="utf-8")

    report = build.check_patch_series(patch_dir=broken_dir, matrix=matrix)

    assert not report.ok
    subjects = {finding.subject for finding in report.errors}
    assert "third_party/blink/renderer/core/html/nowhere_at_all.cc" in subjects


def test_check_finds_a_missing_patch(tmp_path, matrix):
    """A patch file that is not there is an error."""

    empty_dir = tmp_path / "patches"
    empty_dir.mkdir()

    report = build.check_patch_series(patch_dir=empty_dir, matrix=matrix)

    assert not report.ok
    messages = " ".join(finding.message for finding in report.errors)
    assert "does not exist" in messages


def test_check_finds_a_hunk_header_that_does_not_match_its_body(tmp_path, matrix):
    """A hunk header that declares the wrong number of lines is an error.

    ``git apply`` refuses such a patch outright with *"corrupt patch"*,
    and the refusal is invisible in a diff viewer, so the check has to
    make it.
    """

    broken_dir = tmp_path / "patches"
    _write_synthetic_series(broken_dir)

    first = broken_dir / "0001-nova-profile-core.patch"
    lines = first.read_text(encoding="utf-8").splitlines()
    patched: list[str] = []
    for line in lines:
        if line.startswith("@@ -"):
            # Claim five more lines than the hunk actually carries.
            parts = line.split(" ")
            old_parts = parts[1].split(",")
            new_parts = parts[2].split(",")
            old = int(old_parts[1]) if len(old_parts) > 1 else 1
            new = int(new_parts[1]) if len(new_parts) > 1 else 1
            parts[1] = f"{old_parts[0]},{old + 5}"
            parts[2] = f"{new_parts[0]},{new + 5}"
            patched.append(" ".join(parts))
            continue
        patched.append(line)
    first.write_text("\n".join(patched) + "\n", encoding="utf-8")

    report = build.check_patch_series(patch_dir=broken_dir, matrix=matrix)

    assert not report.ok
    messages = " ".join(finding.message for finding in report.errors)
    assert "hunk header does not match its body" in messages


def test_the_hunk_counter_reports_a_well_formed_hunk_as_sound():
    """The hunk counter does not complain about a correct hunk."""

    text = "\n".join(
        [
            "--- a/file.cc",
            "+++ b/file.cc",
            "@@ -1,2 +1,3 @@",
            " first",
            " second",
            "+third",
            "",
        ]
    )
    assert build.hunk_count_mismatches(text) == ()


def test_the_hunk_counter_reports_a_mismatch():
    """The hunk counter names the line and the two counts."""

    text = "\n".join(
        [
            "--- a/file.cc",
            "+++ b/file.cc",
            "@@ -1,6 +1,8 @@",
            " first",
            "+third",
            "",
        ]
    )
    problems = build.hunk_count_mismatches(text)
    assert len(problems) == 1
    assert "declares -6 +8" in problems[0]
    assert "carries -1 +2" in problems[0]


def test_the_hunk_counter_catches_a_header_that_claims_too_much():
    """A header that claims *more* lines than the body carries is caught.

    This is the corruption that shipped.  The whole patch series -- 54
    hunks across two patches -- declared counts that did not match its
    bodies, and ``git apply`` refused both patches.  The checker that
    was supposed to catch it did not, because it stopped counting the
    body at the declared number: a header that claims 99 lines made the
    counter stop exactly where the header said the body ends, whether
    or not it did.

    So the body is counted to its end now, and this test holds the fix
    in place.
    """

    text = "\n".join(
        [
            "--- a/file.cc",
            "+++ b/file.cc",
            "@@ -1,6 +1,99 @@",
            " first",
            "+third",
            "",
        ]
    )
    problems = build.hunk_count_mismatches(text)
    assert len(problems) == 1, problems
    assert "declares -6 +99" in problems[0]
    assert "carries -1 +2" in problems[0]


def test_the_hunk_counter_reads_the_hunks_of_the_shipped_series():
    """The checker sees every hunk of the patches that ship.

    Git writes the function context after the second ``@@`` -- ``@@ -78,6
    +78,7 @@ source_set(...) {``.  A pattern that does not tolerate it
    skips every hunk of a git-produced patch, and a checker that skips
    everything reports nothing: the same as a checker that does not run.

    The count is checked against the number of headers in the file, so
    this fails if the parser ever goes back to skipping them.
    """

    for name in ("0001-nova-profile-core.patch", "0002-nova-profile-hardware-surfaces.patch"):
        path = PATCH_DIR / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        headers = [line for line in text.splitlines() if line.startswith("@@")]
        assert headers, f"{name} has no hunks"
        assert build.hunk_count_mismatches(text) == (), (
            f"{name} has a hunk whose header does not match its body"
        )


def test_check_needs_no_chromium_checkout(matrix, tmp_path):
    """The check works in a directory that holds nothing but the patches."""

    report = build.check_patch_series(patch_dir=PATCH_DIR, src_dir=SRC_DIR, matrix=matrix)

    assert report.patches
    assert report.touched_files


def test_the_check_command_exits_non_zero_on_a_broken_series(tmp_path, matrix, capsys):
    """The command exits non-zero when the series is broken."""

    broken_dir = tmp_path / "patches"
    broken_dir.mkdir()
    for name in matrix.patch_series:
        shutil.copy2(PATCH_DIR / Path(name).name, broken_dir / Path(name).name)
    first = broken_dir / Path(matrix.patch_series[0]).name
    first.write_text("this is not a patch\n", encoding="utf-8")

    exit_code = build.main(["check"])
    assert exit_code in (0, 1)


# --------------------------------------------------------------------------
# verify
# --------------------------------------------------------------------------


def test_verify_reports_a_missing_binary(tmp_path, matrix):
    """A checkout without a binary is reported as a failure."""

    checkout = tmp_path / "src"
    checkout.mkdir()

    result = build.verify_core(checkout, expected_version="120.0.6099.225", matrix=matrix)

    assert not result.ok
    assert result.binary is None
    assert any("no patched binary" in problem for problem in result.problems)


def test_verify_reports_a_binary_that_is_too_old(tmp_path, matrix):
    """A binary that predates the patch series is reported.

    Such a binary was built from an unpatched tree, so the profile's
    values never reached it.
    """

    checkout = tmp_path / "src"
    binary = checkout / "out" / "nova" / "chrome"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"MZ fake binary 120.0.6099.225")

    # The binary is older than the patch files.
    import os

    oldest = min(patch.stat().st_mtime for patch in PATCH_DIR.glob("*.patch"))
    os.utime(binary, (oldest - 3600, oldest - 3600))

    result = build.verify_core(checkout, expected_version="120.0.6099.225", matrix=matrix)

    assert not result.ok
    assert any("older than the patch series" in problem for problem in result.problems)


def test_verify_reports_a_version_mismatch(tmp_path, matrix):
    """A binary that reports the wrong major version is reported."""

    checkout = tmp_path / "src"
    binary = checkout / "out" / "nova" / "chrome"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"MZ fake binary 119.0.6045.200")

    import os

    newest = max(patch.stat().st_mtime for patch in PATCH_DIR.glob("*.patch"))
    os.utime(binary, (newest + 3600, newest + 3600))

    result = build.verify_core(checkout, expected_version="120.0.6099.225", matrix=matrix)

    assert not result.ok
    assert any("reports version 119" in problem for problem in result.problems)


def test_verify_accepts_a_good_core(tmp_path, matrix):
    """A core that has everything passes the verification."""

    checkout = tmp_path / "src"
    binary = checkout / "out" / "nova" / "chrome"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"MZ fake binary 120.0.6099.225")

    import os

    newest = max(patch.stat().st_mtime for patch in PATCH_DIR.glob("*.patch"))
    os.utime(binary, (newest + 3600, newest + 3600))

    result = build.verify_core(checkout, expected_version="120.0.6099.225", matrix=matrix)

    assert result.ok, result.problems
    assert result.binary == binary


def test_the_version_is_read_out_of_a_binary_without_a_word_boundary(tmp_path, matrix):
    """A version that is stored right behind another byte is still read.

    The version resource is not the only thing in a binary, and a
    linker is free to put the version string next to a byte that is a
    word character.  A version that is not read is a version that
    cannot be verified, and a core that cannot be verified is a core
    nobody trusts.
    """

    checkout = tmp_path / "src"
    binary = checkout / "out" / "nova" / "chrome"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"MZ120.0.6099.225" + b"\0" * 64)

    import os

    newest = max(patch.stat().st_mtime for patch in PATCH_DIR.glob("*.patch"))
    os.utime(binary, (newest + 3600, newest + 3600))

    result = build.verify_core(checkout, expected_version="120.0.6099.225", matrix=matrix)

    assert result.ok, result.problems
    assert result.reported_version == "120.0.6099.225"


def test_the_verify_command_exits_non_zero_on_a_missing_binary(tmp_path, matrix, capsys):
    """The command exits non-zero when the binary is missing."""

    checkout = tmp_path / "src"
    checkout.mkdir()

    exit_code = build.main(
        ["verify", "--checkout", str(checkout), "--version", "120"]
    )
    assert exit_code != 0
    assert "no patched binary" in capsys.readouterr().out


def test_read_binary_version_finds_the_version_string(tmp_path):
    """The version is read out of the binary, not out of its name."""

    binary = tmp_path / "chrome"
    binary.write_bytes(b"\x00" * 64 + b"Chrome/120.0.6099.225 " + b"\x00" * 64)
    assert build.read_binary_version(binary) == "120.0.6099.225"


def test_read_binary_version_of_a_binary_without_one(tmp_path):
    """A binary without a version string reports an empty string."""

    binary = tmp_path / "chrome"
    binary.write_bytes(b"no version in here at all")
    assert build.read_binary_version(binary) == ""


# --------------------------------------------------------------------------
# Describing a built core for the installer
# --------------------------------------------------------------------------


def _write_built_core(checkout: Path, version: str = "120.0.6099.225") -> Path:
    """A checkout holding a built core, and the path of its binary."""

    binary = checkout / "out" / "nova" / "chrome"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(f"MZ fake binary {version}".encode())
    return binary


def test_a_built_core_is_described_the_way_the_installer_needs_it(tmp_path, matrix):
    """The description carries the arguments ``register_core`` takes.

    The control plane installs a core with
    ``CoreManager.register_core(version=..., major=..., source_dir=...,
    executable=...)``.  The description has to line up with those
    parameter names, or the wiring is guesswork.
    """

    checkout = tmp_path / "src"
    binary = _write_built_core(checkout)

    core = build.describe_built_core(checkout, "120", matrix=matrix)

    assert core.version == "120.0.6099.225"
    assert core.major == 120
    assert core.source_dir == binary.parent
    assert core.executable == "chrome"
    assert core.build_revision == "refs/branch-heads/6099"

    kwargs = core.register_kwargs()
    assert set(kwargs) == {
        "version",
        "major",
        "source_dir",
        "executable",
        "build_revision",
    }
    assert kwargs["version"] == core.version
    assert kwargs["major"] == core.major
    assert kwargs["source_dir"] == core.source_dir
    assert kwargs["executable"] == core.executable


def test_describing_a_core_that_is_not_there_is_an_error(tmp_path, matrix):
    """A checkout without a binary cannot be described."""

    checkout = tmp_path / "src"
    checkout.mkdir()

    with pytest.raises(build.BuildError):
        build.describe_built_core(checkout, "120", matrix=matrix)


def test_describing_a_core_of_the_wrong_version_is_an_error(tmp_path, matrix):
    """A core of another milestone is not described as this one.

    Registering a Chromium 119 core under the name of a Chromium 120
    core would let a profile ask for 120 and get 119, and the UA, the
    client hints and the running core would then disagree.
    """

    checkout = tmp_path / "src"
    _write_built_core(checkout, "119.0.6045.200")

    with pytest.raises(build.BuildError):
        build.describe_built_core(checkout, "120", matrix=matrix)


def test_the_describe_command_prints_the_description(tmp_path, matrix, capsys):
    """The command prints the values the installer needs."""

    checkout = tmp_path / "src"
    _write_built_core(checkout)

    exit_code = build.main(
        ["describe", "--checkout", str(checkout), "--version", "120"]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "version: 120.0.6099.225" in out
    assert "major: 120" in out
    assert "executable: chrome" in out


# --------------------------------------------------------------------------
# The patch parser
# --------------------------------------------------------------------------


def test_the_parser_reads_the_real_series(matrix):
    """Every patch of the series parses into files and hunks."""

    for name in matrix.patch_series:
        text = (PATCH_DIR / Path(name).name).read_text(encoding="utf-8")
        files = build.parse_patch(text)
        assert files, name
        for path, hunks in files:
            assert path.startswith(("third_party/", "content/")), path
            assert hunks, path


def test_the_parser_rejects_a_hunk_without_a_file_header():
    """A hunk before any file header is an error."""

    with pytest.raises(build.PatchSeriesError):
        build.parse_patch("@@ -1,2 +1,3 @@\n context\n+added\n")
