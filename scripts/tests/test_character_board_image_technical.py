from PIL import Image

from scripts.core.image_technical import character_board_image_problem


def test_board_checks_decoded_canvas_not_file_name(tmp_path):
    path = tmp_path / 'final.png'
    Image.new('RGB', (300, 400)).save(path)
    assert '16:9' in character_board_image_problem(path)

    # These bytes satisfy the technical gate. Panel content still needs review.
    Image.new('RGB', (160, 90)).save(path)
    assert character_board_image_problem(path) is None


def test_board_rejects_unsupported_or_unreadable_media(tmp_path):
    path = tmp_path / 'final.png'
    Image.new('RGB', (160, 90)).save(path, format='BMP')
    assert 'PNG、JPEG 或 WEBP' in character_board_image_problem(path)

    path.write_bytes(b'not an image')
    assert '无法解码' in character_board_image_problem(path)
