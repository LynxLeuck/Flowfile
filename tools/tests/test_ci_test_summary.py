import json
import subprocess
import sys
from pathlib import Path

import pytest


def run_summary(tmp_path, job_outcome, failed_phase=None, invocations=None):
    source = tmp_path / 'evidence.json'
    source.write_text(json.dumps({
        'commit_sha': 'a' * 40, 'run_id': '123', 'attempt': 1,
        'invocations': invocations if invocations is not None else [{
            'suite': 'flowfile_core', 'platform': 'Windows', 'shard': 'core-a',
            'job_outcome': job_outcome, 'failed_phase': failed_phase,
            'junit': 'core-a.xml',
        }],
    }), encoding='utf-8')
    script = Path(__file__).resolve().parents[1] / 'ci_test_summary.py'
    result = subprocess.run([sys.executable, str(script), str(source), str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / 'test-summary.json').read_text(encoding='utf-8'))
    return report, (tmp_path / 'test-summary.md').read_text(encoding='utf-8')


def test_failed_setup_without_xml_is_not_zero_failures(tmp_path):
    report, markdown = run_summary(tmp_path, 'failure', 'setup')
    assert report['schema_version'] == 1
    assert report['attempt'] == 1
    row, = report['invocations']
    assert row['report_status'] == 'missing'
    assert row['counts'] is None
    assert row['job_outcome'] == 'failure'
    assert 'Setup failed · no test report' in markdown


def test_junit_failure_is_visible_in_both_outputs(tmp_path):
    (tmp_path / 'core-a.xml').write_text(
        '<testsuite><testcase classname="test_core" name="test_ok"/>'
        '<testcase classname="test_core" name="test_bad"><failure/></testcase></testsuite>',
        encoding='utf-8',
    )
    report, markdown = run_summary(tmp_path, 'failure', 'test')
    row, = report['invocations']
    assert row['report_status'] == 'available'
    assert row['counts'] == {'passed': 1, 'failed': 1, 'errors': 0, 'skipped': 0}
    assert row['problems'] == [{'classname': 'test_core', 'name': 'test_bad', 'outcome': 'failed'}]
    assert r'test\_core\.test\_bad' in markdown


@pytest.mark.parametrize(('xml', 'status', 'counts'), [
    ('<testsuite/>', 'empty', {'passed': 0, 'failed': 0, 'errors': 0, 'skipped': 0}),
    ('<testsuite>', 'invalid', None),
    ('<not-junit/>', 'invalid', None),
    ('<!DOCTYPE testsuite><testsuite/>', 'invalid', None),
    ('<testsuites><testsuite><testcase><error/></testcase><testcase><skipped/></testcase>'
     '</testsuite></testsuites>', 'available', {'passed': 0, 'failed': 0, 'errors': 1, 'skipped': 1}),
])
def test_junit_evidence_is_classified_independently_of_job_status(tmp_path, xml, status, counts):
    (tmp_path / 'core-a.xml').write_text(xml, encoding='utf-8')
    report, _ = run_summary(tmp_path, 'success')
    row, = report['invocations']
    assert row['job_outcome'] == 'success'
    assert (row['report_status'], row['counts']) == (status, counts)


def test_reporter_keeps_supplied_invocations_without_requiring_windows_names(tmp_path):
    invocations = [
        {'suite': 'new|suite', 'platform': 'Linux', 'shard': str(shard), 'job_outcome': outcome}
        for shard, outcome in enumerate(('success', 'cancelled', 'skipped'))
    ]
    report, markdown = run_summary(tmp_path, 'success', invocations=invocations)
    assert report['invocations'] == [
        {**row, 'report_status': 'missing', 'counts': None, 'problems': []} for row in invocations
    ]
    assert markdown.startswith('# Test summary\n')
    assert markdown.count(r'new\|suite | Linux') == len(invocations)


def test_failure_names_are_escaped_and_raw_output_is_not_copied(tmp_path):
    (tmp_path / 'core-a.xml').write_text(
        '<testsuite><testcase name="café[one|&lt;b&gt;]">'
        '<failure>private diagnostic</failure><system-out>private stdout</system-out>'
        '</testcase></testsuite>', encoding='utf-8',
    )
    report, markdown = run_summary(tmp_path, 'failure')
    assert report['invocations'][0]['problems'][0]['name'] == 'café[one|<b>]'
    assert r'café\[one\|\<b\>\]' in markdown
    assert 'private' not in (json.dumps(report) + markdown)
