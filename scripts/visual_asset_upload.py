"""Select project reference images and normalize historical generation outputs."""

import math
import threading
from pathlib import Path

from PIL import Image, ImageOps



RATIOS = {
    '人物单图': (3, 4),
    '大头照': (3, 4),
    '服装三视图': (16, 9),
    '宠物': (16, 9),
    '场景': (16, 9),
    '物品': (16, 9),
    '产品': (3, 4),
    '风格': (16, 9),
    '其他': (16, 9),
}
PICKER_LOCK = threading.Lock()


def pick_local_files(multiple=False):
    """Get real local paths; ordinary browser file inputs do not provide them."""
    if not PICKER_LOCK.acquire(blocking=False):
        raise ValueError('选图窗口已打开，请先完成当前选择')
    try:
        import tkinter as tk
        from tkinter import filedialog

        try:
            window = tk.Tk()
        except tk.TclError as exc:
            raise ValueError(f'本机选图窗口无法打开：{exc}') from exc
        try:
            window.withdraw()
            window.attributes('-topmost', True)
            window.update()
            options = {'parent': window, 'title': '选择要导入的图片',
                       'filetypes': [('图片', '*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff')]}
            try:
                chosen = filedialog.askopenfilenames(**options) if multiple else filedialog.askopenfilename(**options)
            except tk.TclError as exc:
                raise ValueError(f'本机选图窗口无法完成选择：{exc}') from exc
            return [str(Path(path).resolve()) for path in (chosen if multiple else [chosen]) if path]
        finally:
            window.destroy()
    finally:
        PICKER_LOCK.release()


def crop_to_category(source, destination, category, recognition=None):
    """Crop safely, otherwise pad the full image; never stretch or synthesize content."""
    ratio = RATIOS.get(category)
    if not ratio:
        raise ValueError('图片分类没有尺寸规范')
    with Image.open(source) as original:
        picture = ImageOps.exif_transpose(original)
        width, height = picture.size
        oriented = original.getexif().get(274, 1) != 1
        destination = Path(destination)
        step = min(width // ratio[0], height // ratio[1])
        crop_width, crop_height = ratio[0] * step, ratio[1] * step
        if (crop_width, crop_height) == (width, height):
            if oriented:
                destination.parent.mkdir(parents=True, exist_ok=True)
                picture.save(destination, 'PNG', icc_profile=picture.info.get('icc_profile'))
            return {'status': 'oriented' if oriented else 'already_correct',
                    'width': width, 'height': height, 'ratio': ':'.join(map(str, ratio))}
        focus_x = recognition['focus_x'] if recognition else 0.5
        focus_y = recognition['focus_y'] if recognition else 0.5
        left = max(0, min(width - crop_width, round(focus_x * width - crop_width / 2)))
        top = max(0, min(height - crop_height, round(focus_y * height - crop_height / 2)))
        needs_padding = step < 1
        if recognition:
            box = recognition['critical_box']
            needs_padding |= (box[0] * width < left or box[2] * width > left + crop_width
                              or box[1] * height < top or box[3] * height > top + crop_height)
        elif 1 - crop_width * crop_height / (width * height) > 0.30:
            needs_padding = True
        if needs_padding:
            pad_step = max(math.ceil(width / ratio[0]), math.ceil(height / ratio[1]))
            target_width, target_height = ratio[0] * pad_step, ratio[1] * pad_step
            if target_width * target_height > Image.MAX_IMAGE_PIXELS:
                return {'status': 'skipped', 'reason': '规范后的画布过大，无法安全处理'}
            transparent = 'A' in picture.getbands() or 'transparency' in picture.info
            mode = 'RGBA' if transparent else 'RGB'
            picture = picture.convert(mode)
            corners = [picture.getpixel(p) for p in
                       [(0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)]]
            color = (0, 0, 0, 0) if transparent else tuple(
                round(sum(c[i] for c in corners) / len(corners)) for i in range(3))
            canvas = Image.new(mode, (target_width, target_height), color)
            offset = ((target_width - width) // 2, (target_height - height) // 2)
            canvas.paste(picture, offset)
            destination.parent.mkdir(parents=True, exist_ok=True)
            canvas.save(destination, 'PNG', icc_profile=picture.info.get('icc_profile'))
            return {'status': 'padded', 'reason': '自动补边保留完整主体；未拉伸或生成新内容',
                    'placement': [*offset, width, height], 'width': target_width,
                    'height': target_height, 'ratio': ':'.join(map(str, ratio))}
        destination.parent.mkdir(parents=True, exist_ok=True)
        picture.crop((left, top, left + crop_width, top + crop_height)).save(destination, 'PNG',
                                                                              icc_profile=picture.info.get('icc_profile'))
        return {'status': 'cropped', 'box': [left, top, crop_width, crop_height],
                'width': crop_width, 'height': crop_height, 'ratio': ':'.join(map(str, ratio))}


def validate_dimensions(source, category):
    """Check the actual processed file before it reaches the asset index."""
    ratio = RATIOS.get(category)
    if not ratio:
        raise ValueError('图片分类没有尺寸规范')
    with Image.open(source) as image:
        width, height = image.size
        image.verify()
    if width < 1 or height < 1 or width * ratio[1] != height * ratio[0]:
        raise ValueError(f'处理后的尺寸仍不符合「{category}」{ratio[0]}:{ratio[1]}，未入库')
