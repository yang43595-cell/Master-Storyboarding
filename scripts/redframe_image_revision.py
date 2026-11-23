"""Resume a single Redframe candidate from its existing production request and sources."""
import argparse
import base64
import hashlib
from io import BytesIO
import json
from pathlib import Path
import sys

from PIL import Image

from .core.io import file_lock, read_json, sha256_file, write_bytes, write_json
from .core.project_kickoff import grant_covers_asset, load_kickoff
from .core.redframe_image_ui import (ImageUIError, _canvas_id, _ident, _one, image_browser_session,
                                     image_resume_step)
from .core.redframe_workspace import _image_output_path


def project_path(project, filename):
    path = (project / filename).resolve()
    if not path.is_relative_to(project):
        raise ValueError(f'候选和回执须在项目内：{filename}')
    return path


def revision_plan(project, previous, request):
    old = read_json(previous)
    raw = request.read_bytes()
    spec = json.loads(raw)
    target, = spec['image_targets']
    ast, _, output = target.partition('@')
    if ast != old['target'].partition('@')[0]:
        raise ValueError('单图返修须属于同一资产或分镜补图请求')
    submit = old['image_submission']
    node = _ident(submit['node_id'])
    kickoff = load_kickoff(project)
    canvas, grant = kickoff['canvas'], kickoff['image_generation']
    if (canvas['platform'] != 'redframe' or grant['provider'] != 'redframe'
            or submit['project_id'] != canvas['target_project_id']
            or submit['canvas_id'] != _canvas_id(canvas['target_url'])):
        raise ValueError('原回执与本项目登记的红帧画布不符')
    asset = target.startswith('AST-')
    if (asset and not grant_covers_asset(grant, ast, output)
            or not asset and grant.get('scope') != 'required_assets_and_supplements'):
        raise ValueError('当前生图授权未覆盖本输出')
    settings = submit['settings']
    if (settings['count'] != 1 or grant['model'] != 'Doubao Seedream 5.5'
            or json.loads(settings['model']) != ['volcengineArkVideo', 'doubao-seedream-5-5-261028']):
        raise ValueError('本入口沿用已实测的 Seedream 5.5 单张参数，其他模型走原生入口')
    available = dict(submit['sources'])
    if old.get('file') and old.get('image_result', {}).get('node_id') == node:
        previous_sha = old['file']['sha256']
        rejected = any(row.get('visual_status') == 'failed' and row.get('sha256') == previous_sha
                       for row in old.get('items', []))
        if not rejected:
            available[node] = previous_sha
    sources = {}
    for filename in spec['source_files']:
        sha = sha256_file((project / filename).resolve())
        matches = [i for i, checksum in available.items() if checksum == sha]
        if len(matches) != 1 or matches[0] in sources:
            raise ValueError(f'本轮参考须能唯一复用原回执源卡或原成图：{filename}')
        sources[_ident(matches[0])] = sha
    scene = next((row for row in spec.get('scenes', []) if
                  f"{row['asset_id']}@{row['output_id']}" == target), {})
    prompt = scene.get('prompt') or spec.get('prompt') or spec['brief']
    roles = spec.get('source_roles', ['参考范围见正文。'] if len(sources) == 1 else [])
    if (not prompt.strip() or len(roles) != len(sources)
            or not all(isinstance(role, str) and role.strip() for role in roles)):
        raise ValueError('本图须有准确提示词，多参考须按 source_files 顺序给 source_roles')
    source_assets = {}
    for ident, checksum in sources.items():
        source = (old.get('sources') or {}).get(ident) if isinstance(old.get('sources'), dict) else None
        if isinstance(source, dict) and source.get('asset_id'):
            source_assets[ident] = [source['asset_id'], source.get('output_kind')]
        if ident == node and asset:
            source_assets[ident] = [ast, old.get('output_kind')]
        if scene.get('source', {}).get('sha256') == checksum and scene['source'].get('asset_id'):
            source_assets[ident] = [scene['source']['asset_id'], scene['source'].get('output_kind')]
    return dict(target=target, old_target=old['target'], previous_target=spec.get('previous_target'),
                source_assets=source_assets, output_kind=spec.get('image_output_kinds', {}).get(target),
                project_id=submit['project_id'], canvas_url=canvas['target_url'], original_node=node,
                request_sha256=hashlib.sha256(raw).hexdigest(), sources=sources, source_roles=roles,
                prompt=prompt, settings=settings)


def check_preparation(project, request, plan, actor_id, task_id, context_id):
    from . import stage_context_cli as stages

    skill = 'asset-image-assistant' if plan['target'].startswith('AST-') else 'storyboard-director'
    argv = ['ensure-prepared', '--project', str(project), '--skill', skill,
            '--actor-id', actor_id, '--task-id', task_id]
    if context_id:
        argv.extend(['--context-id', context_id])
    gate = stages.ensure_prepared(stages.parser().parse_args(argv))
    basis = read_json(Path(gate['path']).with_name('task_basis.json'))
    if (not gate['ok'] or gate.get('production_blocked') or basis.get('candidate_trial')
            or Path(basis['request_path']).resolve() != request or sha256_file(request) != plan['request_sha256']):
        raise ValueError('当前准备包未覆盖本次单图请求或阅读尚未完成')


def new_card_point(ui, original_node):
    locator = ui.focus(original_node)
    box = locator.bounding_box()
    point = ui.page.evaluate("""box => {
      const origin = [box.x + box.width + 50, box.y];
      const width = Math.max(160, box.width), height = Math.max(140, box.height);
      const occupied = [...document.querySelectorAll('.vue-flow__node')].map(n=>n.getBoundingClientRect());
      const points = [];
      for (let y=100; y<innerHeight-180; y+=80) for (let x=100; x<innerWidth-220; x+=100) {
        if (x+width < innerWidth-35 && y+height < innerHeight-55 &&
            !occupied.some(r=>r.left<x+width && r.right>x && r.top<y+height && r.bottom>y) &&
            [[0,0],[width,0],[0,height],[width,height]].every(([dx,dy]) =>
            document.elementFromPoint(x+dx,y+dy)?.classList.contains('vue-flow__pane')))
          points.push([x,y]);
      }
      return points.sort((a,b)=>Math.hypot(a[0]-origin[0],a[1]-origin[1])-
        Math.hypot(b[0]-origin[0],b[1]-origin[1]))[0];
    }""", box)
    if not point:
        raise ValueError('本视口没有可核实的候选空位')
    return point


def configure(ui, node, settings):
    current = _one(ui.workspace.read_canvas()['nodes'], node)['data']
    for key in ('model', 'ratio', 'size'):
        if current.get(key) == settings[key]:
            continue
        host = ui.focus(node)
        if key == 'model':
            host.get_by_role('combobox', name='生成模型').click()
            ui.page.get_by_role('option').filter(has_text='Seedream 5.5').click()
        else:
            host.get_by_role('button', name='选择图片比例和分辨率', exact=True).click()
            ui.page.get_by_role('button', name=settings[key], exact=True).click()
        ui._wait_canvas(lambda graph: True if _one(graph['nodes'], node)['data'].get(key) == settings[key]
                        else None, '保存成图参数 ' + key, timeout=60)
        if key != 'model':
            ui.page.keyboard.press('Escape')


def run_revision(ui, project, plan, receipt, save, destination, *, recovered_node=None):
    if not receipt.get('image_submission'):
        # _new_node identifies its own newly persisted card against a before-ID snapshot.
        # Only that small critical section needs serialization across distinct candidates.
        canvas_id = _canvas_id(plan['canvas_url'])
        with file_lock(project / '00_项目管理/执行上下文/红帧建卡' / (canvas_id + '.lock'), timeout_seconds=100):
            if 'previous_node_ids' in receipt and not receipt.get('node_id'):
                created = ui.find_new_nodes(receipt['previous_node_ids'], generation=True)
                ids = [row['id'] for row in created]
                if not recovered_node or recovered_node not in ids:
                    raise ValueError(f'建卡结果待核，原画布新增图片节点 {ids}；核实归属后用 --recover-node 接续')
                candidate = next(row['data'] for row in created if row['id'] == recovered_node)
                if candidate.get('prompt') or (candidate.get('outputs') or {}).get('image', {}).get('value'):
                    raise ValueError('待找回的节点已有其他正文或图片')
                receipt['node_id'] = recovered_node
                save()
            point = (0, 0) if receipt.get('node_id') else new_card_point(ui, plan['original_node'])
            node = ui.resume_image_node(receipt, lambda _: save(), *point)
        ui.rename(node, destination.stem)
        parts = [plan['prompt']]
        for i, (source, role) in enumerate(zip(plan['sources'], plan['source_roles']), 1):
            if source in plan['source_assets']:
                ast, kind = plan['source_assets'][source]
                ui.ensure_asset_card(project, ast, kind, source, plan['sources'][source], receipt, save)
            ui.connect(source, node)
            parts.append(dict(index=i, source_id=source, text=role))
        try:
            body = ui.write_prompt(node, parts)
        except ImageUIError as exc:
            if exc.operation != 'focus image prompt':
                raise
            # No submission has occurred. Refresh and rebind only this node's editor.
            ui.page.reload(wait_until='domcontentloaded')
            body = ui.write_prompt(node, parts)
        configure(ui, node, plan['settings'])
        ui.submit_once(node, settings=plan['settings'], sources=plan['sources'], prompt=body,
                       receipt=receipt, save_receipt=lambda _: save())
    if not receipt.get('image_result'):
        receipt['image_result'] = ui.query(receipt, timeout=180)
        save()
    result = ui.finish_asset_image(project, receipt, save, output_kind=plan['output_kind'])
    ui.page.reload(wait_until='domcontentloaded')
    ui.assert_target()
    node = _one(ui.workspace.read_canvas()['nodes'], result['node_id'])
    if _image_output_path(node) != result['output']:
        raise ValueError('刷新后未找到这次提交的原图')
    # The origin and node-owned output URL have been checked by assert_target/_image_output_path.
    data = ui.page.evaluate("""async ({directory,path}) => {
      const res = await fetch('/api/workspaces/files/read?'+new URLSearchParams({directory,path}),
        {credentials:'same-origin',cache:'no-store',redirect:'error',signal:AbortSignal.timeout(45000)});
      if (!res.ok) throw Error('image read '+res.status);
      const blob=await res.blob();
      return await new Promise((ok,fail)=>{const r=new FileReader();
        r.onload=()=>ok(r.result.split(',')[1]);r.onerror=fail;r.readAsDataURL(blob);});
    }""", dict(directory=ui.workspace.directory, path=result['output']))
    raw = base64.b64decode(data, validate=True)
    with Image.open(BytesIO(raw)) as im:
        im.load()
        suffix = {'PNG': '.png', 'JPEG': '.jpg', 'WEBP': '.webp'}[im.format]
        path = project_path(project, destination.with_suffix(suffix))
        checksum = hashlib.sha256(raw).hexdigest()
        if path.exists():
            if sha256_file(path) != checksum:
                raise ValueError(f'已有其他候选占用路径：{path}')
        else:
            write_bytes(path, raw)
        receipt['file'] = dict(path=path.relative_to(project).as_posix(),
                               sha256=checksum, dimensions=list(im.size))
    receipt['canvas_verified_phase'] = 'after_refresh'
    save()


def existing_receipt(path, plan, binding):
    if not path.exists():
        if plan['target'] != plan['old_target'] and plan['previous_target'] != plan['old_target']:
            raise ValueError('更换输出编号须在本次请求明确绑定 previous_target')
        return dict(target=plan['target'], request_sha256=plan['request_sha256'],
                    revision=binding, output_kind=plan['output_kind'])
    receipt = read_json(path)
    if receipt.get('revision') == binding:
        if (not receipt.get('image_submission') and plan['target'] != plan['old_target']
                and plan['previous_target'] != plan['old_target']):
            raise ValueError('更换输出编号须在本次请求明确绑定 previous_target')
        return receipt
    # Existing production receipts predate the revision command. A recorded submission
    # bound to these exact inputs can be recovered, never submitted again.
    submission = receipt.get('image_submission') or {}
    if ('revision' not in receipt and receipt.get('target') == plan['target']
            and receipt.get('request_sha256') == plan['request_sha256']
            and submission.get('canvas_id') == _canvas_id(plan['canvas_url'])
            and submission.get('project_id') == plan['project_id']
            and submission.get('settings') == plan['settings']
            and list((submission.get('sources') or {}).items()) == list(plan['sources'].items())
            and submission.get('node_id') == receipt.get('node_id') and receipt.get('node_id')):
        return receipt
    raise ValueError('该回执已属于其他请求或建卡归属尚待核实；请核对原单图回执')


def prepare_request(argv):
    """Derive a separately named one-target request while retaining the exact prior sources."""
    parser = argparse.ArgumentParser(description='沿上一版单图回执仅改提示词，另存本轮制作请求')
    for name in ('project', 'previous', 'request'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--from-request', type=Path, help='旧回执同目录找不到唯一同哈希制作请求时指定')
    edit = parser.add_mutually_exclusive_group(required=True)
    edit.add_argument('--replace', nargs=2, metavar=('原句', '新句'))
    edit.add_argument('--new-prompt', help='本图完整修订后的提示词')
    args = parser.parse_args(argv)
    root = args.project.resolve(strict=True)
    previous = project_path(root, args.previous)
    destination = project_path(root, args.request)
    old = read_json(previous)
    candidates = ([project_path(root, args.from_request)] if args.from_request else
                  sorted(previous.parent.glob('*制作请求*.json')))
    matches = [p for p in candidates if p.is_file() and sha256_file(p) == old.get('request_sha256')]
    if len(matches) != 1:
        raise ValueError('旧回执未唯一对应制作请求；用 --from-request 指向同字节原请求')
    source = matches[0]
    if destination.suffix != '.json' or destination in {previous, source}:
        raise ValueError('新请求须另存为项目内 JSON')
    spec = read_json(source)
    target = old['target']
    targets = spec['image_targets']
    if targets.count(target) != 1:
        raise ValueError('旧请求未唯一列出本图目标')
    scenes = [r for r in spec.get('scenes', []) if f"{r['asset_id']}@{r['output_id']}" == target]
    if len(targets) > 1:
        if len(scenes) != 1 or 'source' not in scenes[0] or spec.get('source_roles'):
            raise ValueError('多目标请求缺少该图独立提示词或原件职责；请指定单图制作请求')
        record = scenes[0]['source']
        local = (root / record['path']).resolve()
        suffix = Path(record['path']).parts
        files = [f for f in spec['source_files'] if (root / f).resolve() == local or
                 (Path(f).is_absolute() and not Path(record['path']).is_absolute()
                  and Path(f).parts[-len(suffix):] == suffix)]
        if len(files) != 1 or sha256_file(local) != record['sha256']:
            raise ValueError('同批中本图对应的参考原件不唯一或已换版')
        spec.update(image_targets=[target], source_files=[record['path']], scenes=scenes)
        if 'image_output_kinds' in spec:
            spec['image_output_kinds'] = {target: spec['image_output_kinds'][target]}
    if spec.get('scenes') and len(scenes) != 1:
        raise ValueError('本图场景提示词不唯一')
    row = scenes[0] if scenes else spec
    key = 'prompt' if 'prompt' in row else 'brief'
    before = row[key]
    if args.replace:
        needle, replacement = args.replace
        if not needle or before.count(needle) != 1:
            raise ValueError('原句未唯一命中本图提示词，未写入新请求')
        revised = before.replace(needle, replacement, 1)
    else:
        revised = args.new_prompt
    if not revised.strip() or revised == before:
        raise ValueError('需要有实际改动的提示词')
    row[key] = revised
    # 'brief' is often a round-wide orchestration note. The new request only concerns one image.
    if row is not spec:
        spec['brief'] = revised
    spec['previous_target'] = target
    spec.pop('decision_ref', None)  # the prior feedback is not approval of this change
    with file_lock(destination.with_suffix('.lock')):
        if destination.exists() and read_json(destination) != spec:
            raise ValueError('新请求路径已被另一版占用；请另存，不覆盖已有版本')
        if not destination.exists():
            write_json(destination, spec)
    return dict(target=target, request=str(destination), source=str(source), submits=0)


def main(argv=None, *, host_browser=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['prepare-request']:
        return prepare_request(argv[1:])
    parser = argparse.ArgumentParser(description='核对单图返修；--apply 沿原画布运行一张并保存候选')
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--previous', required=True, type=Path, help='上一版单图红帧回执')
    parser.add_argument('--request', required=True, type=Path, help='本轮单图制作请求')
    parser.add_argument('--receipt', required=True, type=Path, help='本轮单图候选的独立回执')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--recover-node', help='已在原画布核实归属的新节点，仅用于上次建卡未知')
    parser.add_argument('--actor-id')
    parser.add_argument('--task-id')
    parser.add_argument('--context-id')
    args = parser.parse_args(argv)
    project = args.project.resolve(strict=True)
    request, prior, path = [project_path(project, arg) for arg in (args.request, args.previous, args.receipt)]
    if len({request, prior, path}) != 3 or path.suffix != '.json':
        raise ValueError('请提供独立的候选回执 JSON 路径')
    plan = revision_plan(project, prior, request)
    binding = {key: plan[key] for key in ('target', 'request_sha256', 'original_node', 'sources', 'settings')}
    ownership = project_path(project, Path('00_项目管理/执行上下文/单图返修') / (plan['request_sha256'] + '.json'))
    def bound_receipt():
        if ownership.exists():
            return project_path(project, read_json(ownership)['receipt'])
        return path

    if not args.apply:
        path = bound_receipt()
        receipt = existing_receipt(path, plan, binding)
        return dict(target=plan['target'], sources=plan['sources'], settings=plan['settings'],
                    request_sha256=plan['request_sha256'], next_step=image_resume_step(project, receipt),
                    submits=0)
    with file_lock(path.with_suffix('.lock')), file_lock(ownership.with_suffix('.lock')):
        path = bound_receipt()
        if ownership.exists() and not path.exists():
            raise ValueError(f'已登记候选回执缺失，先恢复原文件：{path}')
        receipt = existing_receipt(path, plan, binding)
        if image_resume_step(project, receipt) != 'review_local_file':
            if not receipt.get('image_submission'):
                if not args.actor_id or not args.task_id:
                    raise ValueError('首次提交须沿用本轮 prepare 的 actor-id 和 task-id')
                check_preparation(project, request, plan, args.actor_id, args.task_id, args.context_id)
            if not ownership.exists():
                write_json(path, receipt)
                write_json(ownership, {'receipt': path.relative_to(project).as_posix()})
            if host_browser is None:
                sys.path.insert(0, str(Path.home() / '.local/share/codex-headless-browser'))
                from headless_edge import task_browser as host_browser
            with image_browser_session(host_browser, project_id=plan['project_id'], canvas_url=plan['canvas_url']) as ui:
                run_revision(ui, project, plan, receipt, lambda: write_json(path, receipt),
                             path.with_name(path.stem + '_候选.png'), recovered_node=args.recover_node)
        if not ownership.exists():
            write_json(ownership, {'receipt': path.relative_to(project).as_posix()})
        return dict(target=receipt['target'], receipt=str(path), file=receipt['file'], next_step='review_local_file')


if __name__ == '__main__':
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except (OSError, ValueError, KeyError, RuntimeError, ImportError) as exc:
        sys.exit(str(exc))
