"""Typed helpers for querying the profile registry.

Contains the :class:`ProfileFilter` used to narrow down profile listings and
the :class:`PaginatedResult` generic used when a caller wants a page of
results plus the total number of matches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from novaprofile.schema.enums import SignatureKind, SignatureState

__all__ = ["ProfileFilter", "PaginatedResult", "SignatureRecord"]


@dataclass(frozen=True, slots=True)
class SignatureRecord:
    """A tracked fingerprint signature.

    Mirrors :class:`novaprofile.core.collision.SignatureRecord` so the store
    can return records even when the core package is not importable.

    Attributes:
        kind: The axis this signature belongs to.
        signature: The signature value itself.
        profile_id: The profile that owns the signature.
        seed_epoch: Seed epoch in effect when the signature was produced.
        nudge_step: Nudge step in effect when the signature was produced.
        state: Whether the signature is active, archived or revoked.
        created_at: When the signature was recorded.
    """

    kind: SignatureKind
    signature: str
    profile_id: str
    seed_epoch: int
    nudge_step: int
    state: SignatureState
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ProfileFilter:
    """A composable set of profile query criteria.

    All criteria are optional; an empty filter matches every profile.

    Attributes:
        group_id: Only profiles belonging to this group.
        folder_id: Only profiles belonging to this folder.
        tags: Only profiles carrying *all* of these tags.
        status: Only profiles in this lifecycle status.
        search: Case-insensitive substring matched against the profile name,
            notes and tags.
        created_after: Only profiles created at or after this instant.
        created_before: Only profiles created at or before this instant.
        limit: Maximum number of rows to return.
        offset: Number of rows to skip.
    """

    group_id: str | None = None
    folder_id: str | None = None
    tags: tuple[str, ...] = ()
    status: str | None = None
    search: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int | None = None
    offset: int = 0

    def __post_init__(self) -> None:
        if self.limit is not None and self.limit < 0:
            raise ValueError("limit must be non-negative")
        if self.offset < 0:
            raise ValueError("offset must be non-negative")

    @property
    def is_empty(self) -> bool:
        """True when the filter places no restriction on the result set."""
        return (
            self.group_id is None
            and self.folder_id is None
            and not self.tags
            and self.status is None
            and self.search is None
            and self.created_after is None
            and self.created_before is None
        )


@dataclass(frozen=True, slots=True)
class PaginatedResult[T]:
    """A page of results together with the total number of matches.

    Attributes:
        items: The rows on this page.
        total: Total number of rows matching the query, ignoring paging.
        limit: The page size that was applied (``None`` means unpaged).
        offset: The number of rows skipped before this page.
    """

    items: list[T] = field(default_factory=list)
    total: int = 0
    limit: int | None = None
    offset: int = 0

    @property
    def has_more(self) -> bool:
        """True when more rows exist beyond this page."""
        if self.limit is None:
            return False
        return self.offset + len(self.items) < self.total

    @property
    def page(self) -> int:
        """Zero-based page index implied by ``offset`` and ``limit``."""
        if not self.limit:
            return 0
        return self.offset // self.limit
