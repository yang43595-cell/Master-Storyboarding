"""Compatibility shim; implementation lives in scripts/core/visual_asset_choices.py."""
if __package__:
    from .core.visual_asset_choices import *  # noqa: F401,F403
    from .core.visual_asset_choices import _load_asset_refs  # noqa: F401
else:
    from core.visual_asset_choices import *  # noqa: F401,F403
    from core.visual_asset_choices import _load_asset_refs  # noqa: F401
