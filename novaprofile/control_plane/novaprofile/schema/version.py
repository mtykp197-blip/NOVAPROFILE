"""Browser version schema.

The declared browser version is part of a profile's fingerprint: it decides
the User-Agent, the client hints and the observable API surface.  It is
stored separately from the seed configuration because it is *chosen*, not
derived -- although once chosen it becomes immutable like everything else.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: A full Chromium version, e.g. ``120.0.6099.225``.
VersionString = str

#: The release channel a core was built from.
ReleaseChannelLiteral = str


class BrowserVersion(BaseModel):
    """The Chromium version a profile declares and runs.

    ``major`` is the authoritative field: it selects the capability entry
    from the version registry.  ``full_version`` is the exact build the
    profile claims; it must belong to the same major, because a profile
    whose full version disagrees with its major is an instant inconsistency.
    """

    model_config = ConfigDict(frozen=True)

    major: int = Field(ge=100, le=1000)
    full_version: VersionString = ""
    build_revision: str = ""
    channel: ReleaseChannelLiteral = "custom"
    #: The core that actually serves this profile.  Filled in by the
    #: launcher once a core has been selected.
    core_id: str = ""
    #: Whether the selected core matches ``major`` exactly.
    core_exact: bool = True
    #: Human-readable note about a fallback, for the operator's benefit.
    core_note: str = ""

    @field_validator("full_version")
    @classmethod
    def _valid_full_version(cls, v: str) -> str:
        if not v:
            return v
        parts = v.split(".")
        if not parts or not parts[0].isdigit():
            raise ValueError("full_version must start with a numeric major version")
        for part in parts:
            if not part.isdigit():
                raise ValueError("full_version must contain only numeric components")
        return v

    @property
    def major_string(self) -> str:
        """The major version as a string, for the UA and the brand list."""

        return str(self.major)

    def matches(self, other_major: int) -> bool:
        """Whether this version's major equals ``other_major``."""

        return self.major == other_major


__all__ = ["BrowserVersion", "ReleaseChannelLiteral", "VersionString"]
