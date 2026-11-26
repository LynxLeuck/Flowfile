import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from tools.windows_test_evidence import build_evidence, make_plan, run_suites, unique_object

ROOT = Path(__file__).resolve().parents[2]
CHANGES = dict.fromkeys(('any_backend', 'backend_core', 'backend_worker', 'backend_frame', 'backend_flowfile',
                         'shared', 'test_workflow'), 'false')


@pytest.mark.parametrize(('flags', 'run_all', 'expected'), [
    ({}, False, set()),
    ({'any_backend': 'true', 'backend_core': 'true'}, False, {('core-a', 'core'), ('core-b', 'core')}),
    ({'any_backend': 'true', 'backend_worker': 'true'}, False, {('pkg-other', 'worker')}),
    ({'any_backend': 'true', 'shared': 'true'}, False, None),
    ({'test_workflow': 'true'}, False, None),
    ({}, True, None),
])
def test_selection_preserves_old_windows_filters(flags, run_all, expected):
    config = json.loads((ROOT / '.github/windows-test-shards.json').read_text(encoding='utf-8'))
    plan, matrix = make_plan(config, {**CHANGES, **flags}, run_all)
    assert matrix['include'] == ([{'shard': key} for key in config] if flags or run_all else [])
    assert {(key, suite) for key, suites in plan.items() for suite, spec in suites.items() if spec['expected']} == (
        expected if expected is not None else {(key, suite) for key, suites in config.items() for suite in suites}
    )


def test_new_shard_is_scheduled_and_current_attempt_missing_evidence_remains_visible(tmp_path):
    config = {'new-shard': {'new-suite': {'change': 'shared', 'args': ['shared/tests']}}}
    plan, matrix = make_plan(config, CHANGES, True)
    assert matrix == {'include': [{'shard': 'new-shard'}]}
    stale = tmp_path / 'test-junit-1-windows-new-shard'
    stale.mkdir()
    (stale / 'shard.json').write_text('{"outcome":"success"}', encoding='utf-8')
    params = dict(commit_sha='abc', run_id='42', attempt=2, matrix_outcome='failure', download_outcome='success')
    row, = build_evidence(plan, tmp_path, **params)['invocations']
    assert (row['expectation'], row['job_outcome'], row['junit']) == (
        'expected', 'unknown', 'test-junit-2-windows-new-shard/new-suite.xml',
    )
    current = tmp_path / 'test-junit-2-windows-new-shard'
    current.mkdir()
    (current / 'shard.json').write_text('{"outcome":"failure","setup_failed":true}', encoding='utf-8')
    row, = build_evidence(plan, tmp_path, **params)['invocations']
    assert (row['job_outcome'], row['failed_phase']) == ('failure', 'setup')


def test_discovers_nested_junit_without_inventory_and_ignores_other_attempts(tmp_path):
    for artifact in ('test-junit-2-brand-new-check', 'test-junit-1-old', 'unrelated'):
        directory = tmp_path / artifact / 'nested'
        directory.mkdir(parents=True)
        (directory / 'new-suite.xml').write_text('<testsuite/>', encoding='utf-8')
    row, = build_evidence({}, tmp_path, commit_sha='abc', run_id='42', attempt=2,
                          matrix_outcome='skipped', download_outcome='success')['invocations']
    assert (row['suite'], row['shard'], row['expectation'], row['job_outcome']) == (
        'nested/new-suite', 'brand-new-check', 'observed', 'unknown',
    )


@pytest.mark.parametrize('config', [{}, {'../bad': {}}, {'empty': {}},
                                   {'valid': {'suite': {'change': 'unknown', 'args': ['tests']}}}])
def test_invalid_inventory_fails_even_on_docs_only(config):
    with pytest.raises(ValueError):
        make_plan(config, CHANGES, False)


def test_duplicate_config_key_is_rejected():
    with pytest.raises(ValueError, match='Duplicate'):
        json.loads('{"shard":{},"shard":{}}', object_pairs_hook=unique_object)


def test_runner_preserves_arguments_and_stops_after_failed_suite(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, *, check):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)

    monkeypatch.setattr(subprocess, 'run', fake_run)
    args = ['flowfile_core/tests', '-m', 'not kernel']
    suites = {key: {'expected': key != 'first', 'args': args} for key in ('first', 'second', 'third')}
    assert run_suites(suites, tmp_path) == 1
    assert calls == [['poetry', 'run', 'pytest', *args, f'--junitxml={tmp_path / "second.xml"}', '-o', 'junit_logging=no']]
    assert json.loads((tmp_path / 'outcomes.json').read_text(encoding='utf-8')) == {'second': 'failure'}


@pytest.mark.parametrize(('detect', 'plan', 'expected', 'selected', 'matrix', 'code'), [
    ('success', 'success', 'true', 'true', 'success', 0),
    ('success', 'success', 'true', 'true', 'failure', 1),
    ('success', 'success', 'true', 'true', 'cancelled', 1),
    ('success', 'success', 'true', 'false', 'skipped', 1),
    ('success', 'success', 'false', 'false', 'skipped', 0),
    ('success', 'failure', 'false', '', 'skipped', 1),
    ('failure', 'skipped', 'false', '', 'skipped', 1),
])
@pytest.mark.skipif(not shutil.which('bash'), reason='requires the Actions gate shell')
def test_stable_gate_uses_native_matrix_result(detect, plan, expected, selected, matrix, code):
    jobs = yaml.safe_load((ROOT / '.github/workflows/test.yaml').read_text(encoding='utf-8'))['jobs']
    script = jobs['backend-tests-windows']['steps'][0]['run']
    result = subprocess.run(['bash', '-e', '-c', script], env={
        **os.environ, 'DETECT': detect, 'PLAN': plan, 'EXPECTED': expected, 'SELECTED': selected, 'MATRIX': matrix,
    }, capture_output=True, text=True)
    assert result.returncode == code, result.stderr


def test_partial_rerun_preserves_untouched_jobs_but_not_stale_results(tmp_path):
    from tools.windows_test_evidence import collect_run_evidence

    for attempt, name in [(1, 'untouched'), (1, 'rerun'), (2, 'new check')]:
        directory = tmp_path / f'test-junit-{attempt}-{name}'
        directory.mkdir()
        (directory / 'suite.xml').write_text('<testsuite/>', encoding='utf-8')
    def job(name):
        return {'name': name, 'conclusion': 'success', 'html_url': f'https://example.com/{name}'}

    evidence, jobs = collect_run_evidence(
        tmp_path, attempt=2, commit_sha='abc', run_id='42', download_outcome='success',
        job_pages={1: [job('untouched'), job('rerun')], 2: [job('rerun'), job('new check')]},
    )
    assert {(row['job_name'], row['source_attempt']) for row in evidence['invocations']} == {
        ('untouched', 1), ('new check', 2),
    }
    assert len(jobs) == 3
