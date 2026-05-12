"""SQLAlchemy 2.0 ORM models mirroring the Supabase schema.

The actual schema (tables, indexes, RLS, policies) is owned by Supabase and
defined in ``sql/schema.sql``. These ORM classes describe how the backend
reads and writes the same tables.

Why ``server_default`` instead of Python defaults:
- Inserts coming from the Supabase SQL editor, dashboard, or REST API still
  populate the columns correctly because the default lives in Postgres.
- ORM inserts skip a round-trip on default columns.

UUIDs use ``gen_random_uuid()`` (pgcrypto, enabled by default on Supabase).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

# ── Common column helpers ──────────────────────────────────────────────

def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        PgUUID(as_uuid=True),
        primary_key=True,
        server_default=func.gen_random_uuid(),
    )


def _created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


def _updated_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


# ── Models ─────────────────────────────────────────────────────────────


class UserProfile(Base):
    """Extends Supabase ``auth.users`` with public profile fields."""

    __tablename__ = "user_profiles"

    # PK is the Supabase auth user id; cascade-deletes when the user is removed.
    id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    email: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    def __repr__(self) -> str:
        return f"UserProfile(id={self.id!s}, email={self.email!r})"


class Ad(Base):
    """A single ad row — title, description, image URL, pipeline metadata."""

    __tablename__ = "ads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ads_status_check",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("auth.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    image_model: Mapped[str] = mapped_column(
        String(64),
        server_default="dalle3",
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        server_default="draft",
        nullable=False,
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        # The Python attribute is ``meta`` to avoid colliding with SQLAlchemy's
        # ``metadata`` attribute on declarative classes; the DB column stays
        # ``metadata`` as documented in the spec.
        "metadata",
        JSONB,
        server_default="{}",
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = _updated_at()

    versions: Mapped[list[AdVersion]] = relationship(
        back_populates="ad",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"Ad(id={self.id!s}, title={self.title!r}, status={self.status!r})"


class AdVersion(Base):
    """Append-only snapshot of an Ad — written on every update."""

    __tablename__ = "ad_versions"

    id: Mapped[UUID] = _uuid_pk()
    ad_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("ads.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        server_default="{}",
        nullable=False,
    )
    created_at: Mapped[datetime] = _created_at()

    ad: Mapped[Ad] = relationship(back_populates="versions")

    def __repr__(self) -> str:
        return f"AdVersion(id={self.id!s}, ad_id={self.ad_id!s})"


__all__ = ["Base", "UserProfile", "Ad", "AdVersion"]
