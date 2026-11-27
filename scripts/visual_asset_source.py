"""Compatibility shim; implementation lives in scripts/core/visual_asset_source.py."""
if __package__:
    from .core.visual_asset_source import *  # noqa: F401,F403
else:
    from core.visual_asset_source import *  # noqa: F401,F403
