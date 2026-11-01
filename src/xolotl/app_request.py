"""An app's request on Meru/requests main (`apps/<app>.yaml`) - the approval a trust PR has to stand on (D82). Only
the three fields the check needs, read on their own: Xolotl doesn't share Shikigami's request model."""

from typing import Literal

import yaml

from xolotl.models import Incoming, Value


class AppRequest(Value):
    name: str
    description: str
    removed: bool


class _Metadata(Incoming):
    name: str


class _Spec(Incoming):
    description: str
    removed: bool = False


class _Document(Incoming):
    """The request file as Shikigami writes it; everything but these three fields is ignored."""

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
    return AppRequest(name=doc.metadata.name, description=doc.spec.description, removed=doc.spec.removed)
