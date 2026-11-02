"""Real CLI, synthetic inputs only; no platform writes or generation."""
import copy
import json
from pathlib import Path
import subprocess
import sys

from scripts.canvas_readback import digest
from scripts.tests.test_packaging_identity import product_fixture


def test_formal_cli_separates_local_preflight_native_readback_and_authorization(tmp_path):
    frozen,_=product_fixture(tmp_path)
    frozen_path=tmp_path/'frozen.json';frozen_path.write_text(json.dumps(frozen))
    observed=copy.deepcopy(frozen)
    observed.update(phase='after_refresh',frozen_sha256=digest(frozen))
    observed['drafts'][0]['native_mentions']=[dict(node_id='M1',media_id='MEDIA',
        entity_type='media',native_noneditable_entity=True)]
    readback=tmp_path/'observed.json';readback.write_text(json.dumps(observed))
    def run(*args):
        return subprocess.run([sys.executable,'-B','-m','scripts.canvas_readback','--delivery',
            '--project',str(tmp_path),'--frozen',str(frozen_path),*args],capture_output=True,text=True)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    preflight=run('--preflight');report=json.loads(preflight.stdout)
    assert preflight.returncode==0 and report['ok']
    assert report['native_refresh_binding']=='not_checked' and report['production_ready'] is False
    matched=run('--readback',str(readback));report=json.loads(matched.stdout)
    assert matched.returncode==0 and report['native_refresh_binding']=='selected_fields_match'
    assert report['generation_authorization']=='not_checked' and report['production_ready'] is False
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    observed['drafts'][0]['native_mentions'][0].pop('media_id')
    readback.write_text(json.dumps(observed))
    for _ in range(2):
        failed=run('--readback',str(readback));report=json.loads(failed.stdout)
        assert failed.returncode==1 and not report['ok']
        assert report['native_refresh_binding']=='failed' and report['production_ready'] is False
    ignored=run('--preflight','--readback',str(readback))
    assert ignored.returncode==2 and 'without --preflight' in ignored.stderr
    observed['phase']='before_refresh';readback.write_text(json.dumps(observed))
    assert run('--readback',str(readback)).returncode==2


def test_initialize_is_not_production_readiness_and_refuses_overwrite(tmp_path):
    from scripts.init_desktop_project import initialize
    root=initialize(tmp_path/'empty-new-project')
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    invalid=tmp_path/'unverified.json';invalid.write_text('{}')
    result=subprocess.run([sys.executable,'-B','-m','scripts.canvas_readback','--delivery','--preflight',
        '--project',str(root),'--frozen',str(invalid)],capture_output=True,text=True)
    assert result.returncode!=0 and not json.loads(result.stdout)['ok']
    init=Path(__file__).parents[1]/'init_desktop_project.py'
    retry=subprocess.run([sys.executable,'-B',str(init),'--path',str(root)],capture_output=True,text=True)
    assert retry.returncode==1 and '拒绝覆盖' in retry.stdout
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
