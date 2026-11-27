"""Compatibility shim; implementation lives in scripts/core/visual_asset_product_usage.py."""
if __package__:
    from .core.visual_asset_product_usage import *  # noqa: F401,F403
    from .core.visual_asset_product_usage import (  # noqa: F401
        _matched_references,
        _material_rows,
        _read_reference,
        _same_file,
        _usage_problem,
        _verify_cached_file,
    )
else:
    from core.visual_asset_product_usage import *  # noqa: F401,F403
    from core.visual_asset_product_usage import (  # noqa: F401
        _matched_references,
        _material_rows,
        _read_reference,
        _same_file,
        _usage_problem,
        _verify_cached_file,
    )
