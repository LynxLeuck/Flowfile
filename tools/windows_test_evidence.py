"""Discover expected suites from the workflow, even when no XML was uploaded."""

import json
import os
import re
from pathlib import Path

import yaml

SHARD_PREFIX = 'backend-tests-windows-'
JUNIT = re.compile(r'--junitxml=windows-junit/([\w-]+)\.xml(?=\s|$)')
CHANGE = re.compile(r"needs\.detect-changes\.outputs\.([\w-]+) == 'true'")
RUN_ALL = "github.event.inputs.run_all_tests == 'true'"


def selected(condition, changes, run_all):
    if not condition:
        return True
    values = []
    for clause in condition.strip().removeprefix('${{').removesuffix('}}').split('||'):
        clause = clause.strip()
        match = CHANGE.fullmatch(clause)
        if match:
            values.append(changes.get(match[1]) == 'true')
        elif clause == RUN_ALL:
            values.append(run_all)
        else:
            raise ValueError(f'Unsupported Windows reporting selector: {clause!r}')
    return any(values)


def suite_steps(job_id, definition):
    seen = set()
    for step in definition['steps']:
        step_id = step.get('id', '')
        match = JUNIT.search(step.get('run', ''))
        if not match and not step_id.startswith('test-'):
            continue
        if not match or not step_id.startswith('test-') or match[1] in seen:
            raise ValueError(f'{job_id}: use a test-* step ID and a unique JUnit filename')
        seen.add(match[1])
        yield match[1], step
    if not seen:
        raise ValueError(f'{job_id}: no JUnit-producing suites found')


def build_evidence(needs, jobs, *, run_all, commit_sha, run_id, attempt):
    detect = needs['detect-changes']
    changes = detect['outputs']
    rows = []
    shard_ids = {key for key in jobs if key.startswith(SHARD_PREFIX)}
    if not shard_ids or shard_ids != {key for key in needs if key.startswith(SHARD_PREFIX)}:
        raise ValueError('Windows shard jobs must match the required fan-in dependencies')
    for job_id in sorted(shard_ids):
        job = needs[job_id]
        definition = jobs[job_id]
        shard = job_id.removeprefix(SHARD_PREFIX)
        outputs = job['outputs']
        current = outputs.get('report_attempt') == str(attempt)
        outcomes = json.loads(outputs.get('test_outcomes') or '{}') if current else {}
        job_selected = selected(definition.get('if'), changes, run_all)
        for suite, step in suite_steps(job_id, definition):
            test_selected = selected(step.get('if'), changes, run_all)
            expected = None if detect['result'] != 'success' else (
                job_selected and test_selected
            )
            test = outcomes.get(step['id'])
            if expected is False:
                expectation = 'not selected'
            elif expected is True:
                expectation = 'expected'
            else:
                expectation = 'unknown'
            rows.append({
                'suite': suite, 'platform': 'Windows', 'shard': shard,
                'job_outcome': job['result'], 'expectation': expectation, 'test_outcome': test,
                'failed_phase': 'setup' if current and outputs.get('setup_failed') == 'true' else None,
                'junit': f'windows-junit-{attempt}-{job_id}/{suite}.xml' if expected is not False else None,
            })
    return {'commit_sha': commit_sha, 'run_id': run_id, 'attempt': attempt, 'invocations': rows}


def main():
    jobs = yaml.safe_load(Path('.github/workflows/test.yaml').read_text(encoding='utf-8'))['jobs']
    evidence = build_evidence(
        json.loads(os.environ['NEEDS_JSON']), jobs,
        run_all=os.environ['RUN_ALL'] == 'true', commit_sha=os.environ['GITHUB_SHA'],
        run_id=os.environ['GITHUB_RUN_ID'], attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
    )
    evidence['download_outcome'] = os.environ['DOWNLOAD_OUTCOME']
    Path('windows-evidence').mkdir(exist_ok=True)
    Path('windows-evidence/evidence.json').write_text(json.dumps(evidence), encoding='utf-8')


if __name__ == '__main__':
    main()
