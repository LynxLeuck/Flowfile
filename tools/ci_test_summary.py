"""Render local CI evidence: python tools/ci_test_summary.py evidence.json output-dir.

JUnit paths are relative to the evidence file and must stay in its directory.
The evidence manifest supplies job outcomes; this tool does not decide CI status.
"""

import argparse
import json
import string
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote

MAX_REPORT_BYTES = 16 * 1024 * 1024


def junit_result(directory, filename):
    if not filename:
        return 'missing', None, []
    path = (directory / filename).resolve()
    if not path.is_relative_to(directory):
        return 'invalid', None, []
    try:
        with path.open('rb') as report:
            data = report.read(MAX_REPORT_BYTES + 1)
        # pytest emits UTF-8; reject DTDs rather than expanding custom entities.
        text = data.decode('utf-8-sig')
        if len(data) > MAX_REPORT_BYTES or '<!DOCTYPE' in text:
            return 'invalid', None, []
        root = ET.fromstring(text)
    except FileNotFoundError:
        return 'missing', None, []
    except (OSError, UnicodeError, ET.ParseError):
        return 'invalid', None, []
    if root.tag not in {'testsuite', 'testsuites'}:
        return 'invalid', None, []

    counts: dict[str, int] = dict.fromkeys(('passed', 'failed', 'errors', 'skipped'), 0)
    problems = []
    for case in root.iter('testcase'):
        outcome = next(
            (label for tag, label in (('error', 'errors'), ('failure', 'failed'), ('skipped', 'skipped'))
             if case.find(tag) is not None),
            'passed',
        )
        counts[outcome] += 1
        if outcome in {'errors', 'failed'}:
            problems.append({'classname': case.get('classname', ''), 'name': case.get('name', ''), 'outcome': outcome})
    return ('available' if sum(counts.values()) else 'empty'), counts, problems


def cell(value):
    # Backslash-escape ASCII punctuation so names cannot inject Markdown or HTML.
    return ''.join('\\' + char if char in string.punctuation else char for char in ' '.join(str(value).split())[:240])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    evidence = json.loads(args.evidence.read_text(encoding='utf-8'))
    rows = []
    lines = [
        '# Test summary', '',
        '| Suite | Platform | Shard | Job | Evidence | Passed | Failed | Errors | Skipped |',
        '| --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |',
    ]
    if evidence.get('matrix_outcome'):
        lines[2:2] = [f"Windows matrix result: {cell(evidence['matrix_outcome'])}", '']
    if evidence.get('download_outcome') == 'failure':
        lines[2:2] = ['Artifact download failed; some test evidence may be unavailable.', '']
    if evidence.get('run_url'):
        lines[2:2] = [link('GitHub Actions run', evidence['run_url']), '']
    failures = []
    for invocation in evidence['invocations']:
        status, counts, problems = junit_result(args.evidence.resolve().parent, invocation.get('junit'))
        row = {**invocation, 'report_status': status, 'counts': counts, 'problems': problems}
        rows.append(row)
        label = {'missing': 'no test report', 'invalid': 'invalid test report', 'empty': 'zero tests reported'}.get(status, status)
        if row.get('expectation') == 'not selected':
            label = 'not selected by path filters'
        elif row.get('failed_phase') == 'setup' and row['job_outcome'] == 'failure':
            label = f'Setup failed · {label}'
        elif row.get('test_outcome'):
            label = f"Test step {row['test_outcome']} · {label}"
        values = [row[key] for key in ('suite', 'platform', 'shard', 'job_outcome')]
        columns = list(map(cell, [*values, label, *(counts.values() if counts is not None else ['—'] * 4)]))
        if row.get('job_url'):
            columns[3] += ' · ' + link('job', row['job_url'])
        if row.get('artifact_url'):
            columns[4] += ' · ' + link('artifact', row['artifact_url'])
        lines.append('| ' + ' | '.join(columns) + ' |')
        for problem in problems[:10]:
            name = '.'.join(filter(None, (problem['classname'], problem['name'])))
            failures.append(f"- {cell(row['shard'])} / {cell(row['suite'])}: {cell(name)} ({cell(problem['outcome'])})")
    if evidence.get('observed_jobs'):
        lines.extend(['', '## GitHub Actions jobs observed at report time', ''])
        for job in evidence['observed_jobs']:
            lines.append(f"- {link(job['name'], job['url'])}: {cell(job['conclusion'] or job['status'])}")
    if failures:
        lines.extend(['', '## Test failures and errors', '', *failures])
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'test-summary.json').write_text(
        json.dumps({**evidence, 'schema_version': 1, 'invocations': rows}, indent=2) + '\n', encoding='utf-8'
    )
    (args.output / 'test-summary.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def link(label, url):
    if not url.startswith(('https://', 'http://')):
        return cell(label)
    return f"[{cell(label)}]({quote(url, safe=':/%')})"


if __name__ == '__main__':
    main()
