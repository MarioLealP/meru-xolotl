"""An app's request on Meru/requests main (`apps/<app>.yaml`) - the approval a trust PR has to stand on (D82). Only
the three fields the check needs, read on their own: Xolotl doesn't share Shikigami's request model."""

from dataclasses import dataclass

import yaml


@dataclass(frozen=True)
class AppRequest:
    name: str
    description: str
    removed: bool


def parse(text: str) -> AppRequest:
    """ValueError if it isn't an App request with a name and a description."""
    doc = yaml.safe_load(text)
    if not isinstance(doc, dict) or doc.get("kind") != "App":
        raise ValueError("not an App request")
    name = (doc.get("metadata") or {}).get("name")
    spec = doc.get("spec") or {}
    description, removed = spec.get("description"), spec.get("removed", False)
    if not isinstance(name, str) or not isinstance(description, str) or not isinstance(removed, bool):
        raise ValueError("an App request needs metadata.name, spec.description and a true/false spec.removed")
    return AppRequest(name=name, description=description, removed=removed)
