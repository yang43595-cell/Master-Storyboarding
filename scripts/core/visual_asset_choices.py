"""Explicit project image requirements, separate from immutable story approvals."""
from .asset_choice_rules import (  # noqa: F401
    RELATIVE,
    _apply_production_spec,
    apply_choices,
    confirmed_split_children,
    effective,
    image_decision,
    image_work_items,
    narrative_digest,
    read_choices,
    requirement_digest,
)


def _load_asset_refs():
    from . import asset_refs
    return asset_refs
