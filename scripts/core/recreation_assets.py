"""Match segment appearance to adopted image outputs for its story paragraphs."""
from collections import defaultdict
from pathlib import Path

from .artifacts import project_path
from .asset_refs import effective, read_choices
from .character_assets import final_output_problems, scoped_output_requirements


def matches_product_rendition(root, ref, product_images):
    """Verify a declared product derivative against the locked image pixels."""
    from PIL import Image

    resized = 'resized_product_source_sha256' in ref
    source_key = 'resized_product_source_sha256' if resized else 'lossless_product_source_sha256'
    original = product_images.get(ref.get(source_key))
    if not original or (resized and original['role'] != 'state'):
        return False
    path = project_path(root, ref['path'])
    if not path.is_relative_to(root) or not path.is_file():
        return False
    with Image.open(original['path']) as source, Image.open(path) as rendition:
        if (source.info.get('icc_profile') != rendition.info.get('icc_profile')
                or source.getexif().get(274, 1) != rendition.getexif().get(274, 1)):
            return False
        if resized:
            if (rendition.format != 'PNG' or rendition.width >= source.width
                    or rendition.width * source.height != rendition.height * source.width):
                return False
            expected = source.convert('RGBA').resize(rendition.size, Image.Resampling.LANCZOS)
        else:
            if rendition.format != 'WEBP' or rendition.size != source.size:
                return False
            expected = source.convert('RGBA')
        return expected.tobytes() == rendition.convert('RGBA').tobytes()


def segment_asset_problems(root, story, plan, product_pack, custom_packaging, load_images):
    choices = read_choices(root)
    if not story.get('asset_requirements') and not choices.get('added'):
        return {}
    requirements = effective(story, choices)
    known = {a['asset_id'] for a in requirements}
    needed = {a['asset_id']: set(a['source_paragraph_ids']) for a in requirements
              if a.get('category') in {'CHARACTER', 'LOCATION'} and
              a.get('image_decision', '需要制作') in {'需要制作', '复用现有素材'}}
    if not needed:
        return {}
    finals = scoped_output_requirements(story, choices, needed)
    by_file = defaultdict(list)
    for ast, image in load_images().items():
        if image.get('status') == 'confirmed':
            for out in image.get('outputs', []):
                if out.get('sha256'):
                    by_file[(Path(out['path']), out['sha256'])].append((ast, out))
    product_images = {r['sha256']: r for r in (product_pack or {}).get('references', [])
                   if r['role'] in {'packaging', 'state', 'material'}}
    derivatives = {}
    problems = defaultdict(list)
    present = {}
    paragraph_segments = defaultdict(set)
    for row in plan['segments']:
        if row['mode'] == 'rewrite':
            continue
        sid = row['id']
        present[sid] = set()
        for key in row['appearance']:
            ref = plan['references'].get(key, {})
            if not ref.get('path') or not ref.get('sha256'):
                problems[sid].append(f'{key} 缺参考文件或校验值')
                continue
            matches = by_file.get((project_path(root, ref['path']), ref['sha256']), [])
            if 'asset_id' in ref or 'output_id' in ref:
                matches = [(ast, o) for ast, o in matches
                           if (ast, o['output_id']) == (ref.get('asset_id'), ref.get('output_id'))]
            elif not matches:
                if ref['sha256'] in product_images or key in custom_packaging:
                    continue
                if 'resized_product_source_sha256' in ref or 'lossless_product_source_sha256' in ref:
                    if key not in derivatives:
                        try:
                            derivatives[key] = matches_product_rendition(root, ref, product_images)
                        except (OSError, ValueError):
                            derivatives[key] = False
                    if derivatives[key]:
                        continue
            if len(matches) != 1:
                problems[sid].append(f'{key} 缺唯一已确认外观输出')
                continue
            ast, output = matches[0]
            binding = f"{ast}@{output['output_id']}"
            issues = final_output_problems(finals, [binding], {ast: [output]})
            if issues:
                problems[sid].extend(issues)
                continue
            present[sid].add(ast)
        for paragraph in row['paragraph_ids']:
            paragraph_segments[paragraph].add(sid)

    for row in plan['segments']:
        sid = row['id']
        if sid not in present:
            continue
        # Older plans can split one paragraph into alternating shots. An actor in
        # the reverse shot need not be attached to a close-up of the product.
        if 'visible_asset_ids' in row:
            explicit = row['visible_asset_ids']
            if (not isinstance(explicit, list) or not all(isinstance(ast, str) for ast in explicit)
                    or set(explicit) - known):
                problems[sid].append('visible_asset_ids 须列出本段出镜的有效资产编号')
                continue
            expected = needed.keys() & set(explicit)
        else:
            expected = {ast for ast, scope in needed.items() for p in row['paragraph_ids']
                        if p in scope and len(paragraph_segments[p]) == 1}
        missing = expected - present[sid]
        if missing:
            problems[sid].append('缺本段人物或场景确认输出：' + '、'.join(sorted(missing)))
    return problems
