"""Shared recipe semantics, independent of the coarse/rigged actor backend.

position.z and walk/run to.z denote support elevation, not a pelvis/root offset.
Postures persist through idle, turn and look_at; only a posture-changing action
replaces them. stand_up leaves the seated support contract at its start.
"""
from __future__ import annotations

HOLD_PRESETS = {"idle", "turn", "look_at"}
POSTURE_PRESETS = {"sit", "stand", "stand_up", "walk", "run", "crouch", "bend_over",
                   "lie_back", "lie_side_left", "lie_side_right"}


def ordered_actions(actor: dict) -> list[dict]:
    return sorted(actor.get("actions", []), key=lambda action: float(action["start"]))


def action_frames(action: dict, fps: int) -> tuple[int, int]:
    return (max(1, round(float(action["start"]) * fps) + 1),
            round((float(action["start"]) + float(action["duration"])) * fps) + 1)


def actor_target_height(actor: dict, *, coarse: bool = False) -> float | None:
    """Explicit height includes the whole visible rest geometry (also adornments).

    Legacy coarse scale remains a multiplier of its original role geometry;
    legacy imported actors retain the historical 2.15 m scale reference.
    """
    if actor.get("height_m") is not None:
        return float(actor["height_m"])
    if actor.get("height_cm") is not None:
        return float(actor["height_cm"]) / 100.0
    return None if coarse else 2.15 * float(actor.get("scale", 1.0))


def effective_actor_state(actor: dict, frame: int, fps: int) -> dict:
    support = float(actor.get("position", [0, 0, 0])[2])
    posture, posture_action = "stand", None
    for action in ordered_actions(actor):
        start, end = action_frames(action, fps)
        if frame < start:
            break
        preset = action["preset"]
        if preset in POSTURE_PRESETS:
            posture = ("stand" if frame >= end else "stand_up") if preset == "stand_up" else preset
            posture_action = action
        if preset in {"walk", "run"} and "to" in action:
            destination = float(action["to"][2])
            progress = min(max((frame - start) / max(end - start, 1), 0.0), 1.0)
            support += (destination - support) * progress
        elif preset.startswith("lie_"):
            support = float(action.get("surface_height", support))
    return {"posture": posture, "action": posture_action, "support_z": support,
            "seated": posture == "sit"}
