"""Mechanical image checks shared by the asset Web UI and final delivery."""


def technical_check(path, ratio):
    from PIL import Image
    with Image.open(path) as image:
        image.load()
        width, height = image.size
        fmt = image.format
    if ratio is None:
        passed = width > height
        verdict = '横版，视觉质量待你确认' if passed else '人物拼版须横向排列，请检查两张源图'
    else:
        a, b = map(int, ratio.split(':'))
        passed = abs(width / height - a / b) / (a / b) <= 0.02
        verdict = '比例符合要求，视觉质量待你确认' if passed else '比例不符合要求，请重做'
    return {'passed': passed, 'width': width, 'height': height, 'format': fmt,
            'expected_ratio': ratio, 'message': f'{width}×{height} · {fmt} · ' + verdict}


def character_board_image_problem(path):
    """Check the delivered canvas and decoded format. Visual review covers its layout."""
    try:
        from PIL import Image
    except ImportError:
        return '核对最终人物1×4图片需要 Pillow，请使用已安装图片资产依赖的 Python'
    try:
        check = technical_check(path, None)
    except (OSError, ValueError, Image.DecompressionBombError):
        return '最终人物1×4图片无法解码'
    if check['format'] not in {'PNG', 'JPEG', 'WEBP'}:
        return '最终人物1×4图片须为 PNG、JPEG 或 WEBP'
    if not check['passed']:
        return f'最终人物1×4须为横向拼版，实测 {check["width"]}×{check["height"]}'
    return None
