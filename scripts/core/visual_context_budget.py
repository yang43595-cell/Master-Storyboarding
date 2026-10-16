"""图片载荷预算检查：确保交付文本不携带任何图片内容载荷。

本模块是确定性工具层（第 ② 层）的一部分，供 `build-image-review-delivery.py` 等调用，
防止审核/交付文本中出现 Base64、`data:image` 或 `image_url` 这类会污染上下文、违背
"图片载荷不进上下文"红线的标记。
"""

from __future__ import annotations

import re

try:
    from .errors import VisualContextBudgetError
except ImportError:  # pragma: no cover - 直接执行时的回退
    from errors import VisualContextBudgetError

# 与 `stage_context_cli.py` 的 BANNED["图片载荷"] 保持一致，避免两处口径漂移。
_PAYLOAD_RE = re.compile(r"data:image|image_url|base64[,;]", re.IGNORECASE)

__all__ = ["VisualContextBudgetError", "assert_payload_free"]


def assert_payload_free(text: str) -> None:
    """若文本包含图片载荷标记，则抛 `VisualContextBudgetError`。"""
    if _PAYLOAD_RE.search(text):
        raise VisualContextBudgetError(
            "交付文本包含图片载荷（data:image / image_url / base64），不得写入上下文包或用户审核清单。"
        )
