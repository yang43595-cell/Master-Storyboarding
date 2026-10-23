"""Blender 版本适配的唯一出口。

runner.py 只通过这里调用随版本变化的 API：Blender 4.2 与 5.x 之间引擎枚举、视频输出
开关、分层 Action、形态键等接口都改过名或改过结构。适配只写在本文件，避免同类补丁
散落到 runner 各处（历史上有 5 处各自打补丁，且定义位置晚于使用位置）。
"""

from __future__ import annotations

import bpy

# 引擎优先级：4.2–4.x 用 BLENDER_EEVEE_NEXT，5.x 又改回 BLENDER_EEVEE
_ENGINE_CANDIDATES = ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE", "BLENDER_WORKBENCH")


def engine_name() -> str:
    available = {item.identifier for item in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    for name in _ENGINE_CANDIDATES:
        if name in available:
            return name
    return "BLENDER_WORKBENCH"


def _set_media_type(image_settings, media_type: str) -> None:
    if "media_type" in image_settings.bl_rna.properties:
        try:
            image_settings.media_type = media_type
        except TypeError:
            pass


def use_still_output(scene, fmt: str = "PNG") -> None:
    """切到图片输出。Blender 5.x 从视频切回图片时必须先把 media_type 设回 IMAGE。"""
    _set_media_type(scene.render.image_settings, "IMAGE")
    scene.render.image_settings.file_format = fmt


def use_video_output(scene, container: str = "MPEG4", codec: str = "H264") -> None:
    """切到视频输出（5.x 需先把 media_type 设为 VIDEO，FFMPEG 才可用）。"""
    _set_media_type(scene.render.image_settings, "VIDEO")
    scene.render.image_settings.file_format = "FFMPEG"
    scene.render.ffmpeg.format = container
    scene.render.ffmpeg.codec = codec


def action_fcurves(action):
    """取 Action 的曲线。Blender 4.4+/5.x 的分层 Action 没有 action.fcurves。"""
    curves = list(getattr(action, "fcurves", []) or [])
    if curves:
        return curves
    for layer in getattr(action, "layers", []) or []:
        for strip in getattr(layer, "strips", []) or []:
            for bag in getattr(strip, "channelbags", []) or []:
                curves.extend(getattr(bag, "fcurves", []) or [])
    return curves


def clear_shape_keys(obj) -> None:
    """清掉形态键，使修改器可以应用（MPFB 人体模板自带形态键）。"""
    mesh = obj.data
    if mesh.shape_keys is None:
        return
    for key_block in list(mesh.shape_keys.key_blocks)[1:]:
        obj.shape_key_remove(key_block)
    if hasattr(obj, "shape_key_clear"):
        obj.shape_key_clear()
