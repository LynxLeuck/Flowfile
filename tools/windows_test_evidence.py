"""Build the Windows pilot manifest from native needs outputs and current-attempt XML."""

import json
import os
from pathlib import Path


def build_evidence(needs, inventory, *, run_all, commit_sha, run_id, attempt):
    detect = needs['detect-changes']
    changes = detect['outputs']
    rows = []
    for shard, suites in sorted(inventory.items()):
        job = needs[f'backend-tests-windows-{shard}']
        outputs = job['outputs']
        current = outputs.get('report_attempt') == str(attempt)
        for suite, change in sorted(suites.items()):
            expected = None if detect['result'] != 'success' else (
                run_all or changes.get('test_workflow') == 'true' or (
                    changes.get('any_backend') == 'true' and
                    any(changes.get(key) == 'true' for key in (change, 'shared'))
                )
            )
            test = outputs.get(f'test_{suite}') if current else None
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
                'junit': f'windows-junit-{attempt}-{shard}/{suite}.xml' if expected is not False else None,
            })
    return {'commit_sha': commit_sha, 'run_id': run_id, 'attempt': attempt, 'invocations': rows}


def main():
    evidence = build_evidence(
        json.loads(os.environ['NEEDS_JSON']), json.loads(os.environ['WINDOWS_INVENTORY']),
        run_all=os.environ['RUN_ALL'] == 'true', commit_sha=os.environ['GITHUB_SHA'],
        run_id=os.environ['GITHUB_RUN_ID'], attempt=int(os.environ['GITHUB_RUN_ATTEMPT']),
    )
    evidence['download_outcome'] = os.environ['DOWNLOAD_OUTCOME']
    Path('windows-evidence').mkdir(exist_ok=True)
    Path('windows-evidence/evidence.json').write_text(json.dumps(evidence), encoding='utf-8')


if __name__ == '__main__':
    main()
