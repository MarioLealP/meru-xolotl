"""The two kinds of data model in Xolotl - both pydantic, immutable and strictly typed (no "7" for 7, no "yes" for
true): its own values, and what it reads from outside (Gitea's answers, request files), checked on the way in."""

from pydantic import BaseModel, ConfigDict


class Value(BaseModel):
    """Xolotl's own objects: an unknown field is a bug."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Incoming(BaseModel):
    """Data from outside: only the fields Xolotl uses are read and checked, the rest ignored."""

    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")
