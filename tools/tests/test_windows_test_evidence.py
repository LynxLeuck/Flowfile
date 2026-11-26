import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.windows_test_evidence import build_evidence

WORKFLOW = Path(__file__).resolve().parents[2] / '.github/workflows/test.yaml'


def workflow_jobs():
    return yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']


def manifest(needs, jobs, run_all=False):
    return build_evidence(needs, jobs, run_all=run_all, commit_sha='abc', run_id='42', attempt=2)


def needs_for(jobs, detect_result='success', changes=None):
    gate = jobs['backend-tests-windows']
    needs = {key: {'result': 'skipped', 'outputs': {}} for key in gate['needs']}
    needs['detect-changes'] = {'result': detect_result, 'outputs': changes or {}}
    return needs


@pytest.mark.parametrize(('detect_result', 'changes', 'run_all', 'expected'), [
    ('success', {}, False, set()),
    ('success', {'any_backend': 'true', 'backend_core': 'true'}, False, {'core'}),
    ('success', {'any_backend': 'true', 'shared': 'true'}, False, {'core', 'worker'}),
    ('success', {'test_workflow': 'true'}, False, {'core', 'worker'}),
    ('success', {}, True, {'core', 'worker'}),
    ('failure', {}, True, set()),
])
def test_workflow_discovery_and_path_filters(detect_result, changes, run_all, expected):
    flags = " || needs.detect-changes.outputs.test_workflow == 'true' || github.event.inputs.run_all_tests == 'true'"
    jobs = {}
    for suite in ('core', 'worker'):
        jobs[f'backend-tests-windows-{suite}'] = {
            'if': "needs.detect-changes.outputs.any_backend == 'true'" + flags,
            'steps': [{
                'id': f'test-{suite}', 'run': f'pytest --junitxml=windows-junit/{suite}.xml',
                'if': f"needs.detect-changes.outputs.backend_{suite} == 'true' || "
                      "needs.detect-changes.outputs.shared == 'true'" + flags,
            }],
        }
    jobs['backend-tests-windows'] = {'needs': ['detect-changes', *jobs]}
    rows = manifest(needs_for(jobs, detect_result, changes), jobs, run_all)['invocations']
    assert len(rows) == 2
    assert {row['suite'] for row in rows if row['expectation'] == 'expected'} == expected
    if detect_result == 'failure':
        assert {row['expectation'] for row in rows} == {'unknown'}


def test_renamed_shard_and_added_suite_need_no_separate_inventory():
    jobs = workflow_jobs()
    gate = jobs['backend-tests-windows']
    old = next(job for job in gate['needs'] if job.startswith('backend-tests-windows-'))
    new = 'backend-tests-windows-renamed'
    jobs[new] = jobs.pop(old)
    jobs[new]['steps'].append({'id': 'test-extra', 'run': 'pytest --junitxml=windows-junit/extra.xml'})
    with pytest.raises(ValueError, match='match the required fan-in'):
        manifest(needs_for(jobs), jobs)
    gate['needs'][gate['needs'].index(old)] = new
    needs = needs_for(jobs)
    needs[new] = {'result': 'failure', 'outputs': {
        'report_attempt': '2', 'test_outcomes': '{"test-extra":"skipped"}', 'setup_failed': 'true',
    }}
    row = next(row for row in manifest(needs, jobs, True)['invocations'] if row['suite'] == 'extra')
    assert row['shard'] == 'renamed'
    assert row['junit'] == f'windows-junit-2-{new}/extra.xml'
    assert row['test_outcome'] == 'skipped'
    assert row['failed_phase'] == 'setup'
    needs[new]['outputs']['report_attempt'] = '1'
    row = next(row for row in manifest(needs, jobs, True)['invocations'] if row['suite'] == 'extra')
    assert row['test_outcome'] is None
    assert row['failed_phase'] is None


@pytest.mark.parametrize(('detect', 'selected', 'statuses', 'code'), [
    ('success', 'true', ['success', 'success'], 0),
    ('success', 'true', ['success', 'failure'], 1),
    ('success', 'true', ['success', 'cancelled'], 1),
    ('success', 'true', ['success', 'skipped'], 1),
    ('success', 'false', ['skipped', 'skipped'], 0),
    ('success', 'false', ['skipped', 'success'], 1),
    ('failure', 'false', ['skipped', 'skipped'], 1),
    ('success', 'true', [], 1),
])
@pytest.mark.skipif(not (shutil.which('bash') and shutil.which('jq')), reason='requires the Actions gate tools')
def test_required_gate_uses_declared_shard_dependencies(detect, selected, statuses, code):
    gate_script = workflow_jobs()['backend-tests-windows']['steps'][0]['run']
    needs = {f'backend-tests-windows-shard-{i}': {'result': status} for i, status in enumerate(statuses)}
    result = subprocess.run(['bash', '-e', '-c', gate_script], env={
        **os.environ, 'DETECT': detect, 'SHARDS_EXPECTED': selected, 'SHARD_NEEDS': json.dumps(needs),
    }, capture_output=True, text=True)
    assert result.returncode == code, result.stderr
