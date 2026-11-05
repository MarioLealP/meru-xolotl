"""An app's request on Meru/requests main (`apps/<app>.yaml`) - the approval a trust PR has to stand on (D82).

Only the fields the check needs, read on their own: Xolotl doesn't share Shikigami's request model.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Literal

import yaml
from pydantic import Field, StringConstraints

from xolotl.models import Incoming, Value


class AppRequest(Value):
    """The request file's fields the check reads, flattened."""

    name: str
    description: str
    removed: bool
    owner: str = ""  # the marker PR's owner lines (D81)
    secrets: tuple[str, ...] = ()  # the names the marker PR wires as env vars (D99)
    entries: tuple[tuple[str, str], ...] = ()  # (name, OpenBao entry) for the names not in env (D123)
    removed_at: datetime | None = None  # Xolotl's own stamp (D105), UTC; Charon's retention counts from it
    retention: timedelta = timedelta(days=30)


Days = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]{0,3}d$")]
# env, or env-<yyyymmdd>-<6 hex> from a change (D123) - checked here too: it becomes a Kubernetes name in the marker
Entry = Annotated[str, StringConstraints(pattern=r"^env(-[0-9]{8}-[0-9a-f]{6})?$")]


class _Metadata(Incoming):
    name: str
    removed_at: datetime | None = Field(default=None, alias="removedAt")  # YAML reads a timestamp as a datetime


class _Secret(Incoming):
    name: str
    entry: Entry = "env"


class _Spec(Incoming):
    description: str
    owner: str = ""
    secrets: list[_Secret] = Field(default_factory=list)
    removed: bool = False
    retention: Days = "30d"  # the same default as the request format


class _Document(Incoming):
    """The request file as Shikigami reads it; everything but these fields is ignored."""

    kind: Literal["App"]
    metadata: _Metadata
    spec: _Spec


def parse(text: str) -> AppRequest:
    """ValueError (pydantic's ValidationError is one) if it isn't an App request with a name and a description."""
    try:
        loaded = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ValueError(f"not YAML: {e}") from e
    doc = _Document.model_validate(loaded)
    removed_at = doc.metadata.removed_at
    if removed_at is not None:  # a timestamp without a zone is UTC, as stamp.py writes it
        removed_at = (removed_at if removed_at.tzinfo else removed_at.replace(tzinfo=UTC)).astimezone(UTC)
    return AppRequest(
        name=doc.metadata.name,
        description=doc.spec.description,
        removed=doc.spec.removed,
        owner=doc.spec.owner,
        secrets=tuple(secret.name for secret in doc.spec.secrets),
        entries=tuple((s.name, s.entry) for s in doc.spec.secrets if s.entry != "env"),
        removed_at=removed_at,
        retention=timedelta(days=int(doc.spec.retention.removesuffix("d"))),
    )
