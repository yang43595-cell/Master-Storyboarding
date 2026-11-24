"""Check source windows and prompt exclusions for depth-reference delivery."""

DEPTH_REFERENCE_EXCLUSION = (
    "深度视频只参考构图、机位、运镜、站位和动作节拍。"
    "忽略其中的字幕、贴纸、界面文字、水印、旧商品及包装形态，不沿用参考原声。"
)


def product_visible_frames(plan, *, required=False):
    """Return the visually reviewed product windows on the source time axis."""
    windows = plan.get("product_visible_frames")
    if windows is None and not required:
        return None
    if not isinstance(windows, list) or any(
        not isinstance(pair, list) or len(pair) != 2
        or any(type(frame) is not int for frame in pair)
        or not plan["first_frame"] <= pair[0] < pair[1] <= plan["end_frame"]
        for pair in windows
    ):
        raise ValueError("复刻计划须实看原片并登记产品可见帧窗 product_visible_frames")
    return windows


def require_product_free_window(start, end, windows, label):
    if windows is not None and any(start < right and end > left for left, right in windows):
        raise ValueError(f"{label} 深度参考覆盖产品可见帧，须先实际切除，再使用同款产品参考")


def check_depth_coverage(plan, windows):
    """Keep normal shots out of the product-only portions of a generation group."""
    ordered = sorted(windows)
    for segment in plan["segments"]:
        mode = segment["mode"]
        start, end = segment["frames"]
        label = segment["id"]
        if mode == "text":
            reason = segment.get("depth_omission_reason")
            if not isinstance(reason, str) or not reason.strip():
                raise ValueError(f"{label} 未保留剧情深度，须补深度或登记本窗缺少可用深度的具体原因")
            if any(start < right and end > left for left, right in ordered):
                raise ValueError(f"{label} 的纯剧情窗覆盖了产品窗，须按镜头拆分并为产品段绑定同款参考")
        if mode not in {"product", "product-display"}:
            continue
        covered_until = start
        for left, right in ordered:
            if left > covered_until or covered_until >= end:
                break
            covered_until = max(covered_until, right)
        if covered_until < end:
            raise ValueError(f"{label} 自第 {covered_until} 帧起有未登记为产品窗的画面，须拆出正常剧情并保留同窗深度")


def check_depth_group(plan, group, windows):
    """The entire file is uploaded, so a clean logical sub-window is insufficient."""
    depth = [key for key in group["reference_keys"] if plan["references"][key]["role"] == "depth"]
    for key in depth:
        ref = plan["references"][key]
        start, length = ref.get("source_start_frame"), ref.get("frame_count")
        if type(start) is not int or type(length) is not int or length <= 0:
            raise ValueError(f"{key} 缺少实传深度片的原片起点或帧数")
        require_product_free_window(start, start + length, windows, key)
    if depth and DEPTH_REFERENCE_EXCLUSION not in group["prompt"]:
        raise ValueError(f"{group['id']} 正文须写明深度参考范围，并明确忽略其中的字幕等内容")
