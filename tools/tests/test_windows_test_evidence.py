import pytest

from tools.windows_test_evidence import build_evidence


@pytest.mark.parametrize(('detect_result', 'changes', 'run_all', 'expected'), [
    ('success', {}, False, ('not selected', 'not selected')),
    ('success', {'any_backend': 'true', 'backend_core': 'true'}, False, ('expected', 'not selected')),
    ('success', {'any_backend': 'true', 'shared': 'true'}, False, ('expected', 'expected')),
    ('success', {'test_workflow': 'true'}, False, ('expected', 'expected')),
    ('success', {}, True, ('expected', 'expected')),
    ('failure', {}, True, ('unknown', 'unknown')),
])
def test_windows_manifest_selection_and_attempt_isolation(detect_result, changes, run_all, expected):
    needs = {
        'detect-changes': {'result': detect_result, 'outputs': changes},
        'backend-tests-windows-one': {
            'result': 'success', 'outputs': {'report_attempt': '1', 'test_core': 'success'},
        },
        'backend-tests-windows-two': {
            'result': 'failure', 'outputs': {'report_attempt': '2', 'setup_failed': 'true', 'test_worker': 'skipped'},
        },
    }
    report = build_evidence(
        needs, {'one': {'core': 'backend_core'}, 'two': {'worker': 'backend_worker'}},
        run_all=run_all, commit_sha='abc', run_id='42', attempt=2,
    )
    first, second = report['invocations']
    assert (first['expectation'], second['expectation']) == expected
    assert first['test_outcome'] is None
    assert first['junit'] == (None if expected[0] == 'not selected' else 'windows-junit-2-one/core.xml')
    assert second['job_outcome'] == 'failure'
    assert second['test_outcome'] == 'skipped'
    assert second['failed_phase'] == 'setup'
