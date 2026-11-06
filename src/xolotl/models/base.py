"""The kinds of data model in Xolotl - all pydantic, immutable and strictly typed (no "7" for 7, no "yes" for true).

Its own values, what it reads from outside (Gitea's answers, request files), checked on the way in, and the services
that hold its clients.
"""

from pydantic import BaseModel, ConfigDict


class Value(BaseModel):
    """Xolotl's own objects: an unknown field is a bug."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class Service(Value):
    """Holds dependencies rather than data, so its fields may be clients and locks, kept as they are."""

    model_config = ConfigDict(arbitrary_types_allowed=True)


class Incoming(BaseModel):
    """Data from outside: only the fields Xolotl uses are read and checked, the rest ignored."""

    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")
