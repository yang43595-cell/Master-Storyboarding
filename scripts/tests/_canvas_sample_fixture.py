"""Shared sample canvas fixture so test_canvas_readback and test_delivery_preflight stay acyclic."""
from __future__ import annotations

import copy

from scripts.canvas_readback import digest


def sample():
    frozen = {
        "target_url": "https://fixture.invalid/canvas/one",
        "drafts": [
            {
                "node_id": "draft-13",
                "prompt": "body with native chip labels",
                "parameters": {"duration": 16, "aspect": "9:16", "model": "fixture"},
                "ordered_media": [
                    {"node_id": "image-1", "media_id": "m1"},
                    {"node_id": "image-2", "media_id": "m2"},
                    {"node_id": "image-1", "media_id": "m1"},
                ],
            }
        ],
    }
    observed = {**copy.deepcopy(frozen), "phase": "after_refresh", "frozen_sha256": digest(frozen)}
    return frozen, observed
