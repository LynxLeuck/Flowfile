"""Plan, execute and report the Windows test matrix from one shard definition."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

CONFIG = Path('.github/windows-test-shards.json')
REPORTS = Path('windows-junit')
EVIDENCE = Path('windows-evidence')
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate configuration key: {key}')
        result[key] = value
    return result


def make_plan(config, changes, run_all):
    if not isinstance(config, dict) or not 1 <= len(config) <= 256:
        raise ValueError('Configure 1–256 Windows shards')
    if any(changes.get(key) not in {'true', 'false'} for key in ('any_backend', 'shared', 'test_workflow')):
        raise ValueError('Missing or invalid Windows path-filter outputs')
    force = run_all or changes.get('test_workflow') == 'true'
    enabled = force or changes.get('any_backend') == 'true'
    plan = {}
    for shard, suites in config.items():
        if not NAME.fullmatch(shard) or not isinstance(suites, dict) or not suites:
            raise ValueError(f'Invalid shard: {shard}')
        plan[shard] = {}
        for suite, definition in suites.items():
            if not NAME.fullmatch(suite) or not isinstance(definition, dict) or set(definition) != {'change', 'args'}:
                raise ValueError(f'Invalid suite: {shard}/{suite}')
            change, args = definition['change'], definition['args']
            if not isinstance(change, str) or changes.get(change) not in {'true', 'false'} or not isinstance(args, list) or not args or not all(
                isinstance(arg, str) and arg and not arg.startswith(('--junitxml', '--junit-xml')) for arg in args
            ):
                raise ValueError(f'Invalid change filter or pytest arguments: {shard}/{suite}')
            plan[shard][suite] = {
                'args': args,
                'expected': enabled and (force or changes[change] == 'true' or changes.get('shared') == 'true'),
            }
    # Preserve the original behavior: when backend CI is enabled, all shards
    # perform setup, even if a particular shard has no matching pytest suite.
    matrix = {'include': [{'shard': shard} for shard in plan] if enabled else []}
    return plan, matrix


def run_suites(suites, directory):
    directory.mkdir(exist_ok=True)
    outcomes = {}
    for suite, spec in suites.items():
        if not spec['expected']:
            continue
        command = ['poetry', 'run', 'pytest', *spec['args'],
                   f'--junitxml={directory / (suite + ".xml")}', '-o', 'junit_logging=no']
        print(f'Running Windows suite: {suite}', flush=True)
        code = subprocess.run(command, check=False).returncode
        outcomes[suite] = 'success' if code == 0 else 'failure'
        (directory / 'outcomes.json').write_text(json.dumps(outcomes), encoding='utf-8')
        if code:
            return code  # Preserve the old default success() gating of later suites.
    return 0


def read_metadata(path):
    try:
        with path.open('rb') as source:
            value = json.loads(source.read(65537))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def build_evidence(plan, directory, *, commit_sha, run_id, attempt, matrix_outcome, download_outcome):
    rows = []
    claimed = set()
    for shard, suites in plan.items():
        artifact = f'test-junit-{attempt}-windows-{shard}'
        metadata = read_metadata(directory / artifact / 'shard.json')
        outcomes = read_metadata(directory / artifact / 'outcomes.json')
        for suite, spec in suites.items():
            expected = spec['expected']
            junit = f'{artifact}/{suite}.xml'
            claimed.add(junit)
            rows.append({
                'suite': suite, 'platform': 'Windows', 'shard': shard,
                'job_outcome': metadata.get('outcome', 'skipped' if matrix_outcome == 'skipped' else 'unknown'),
                'expectation': 'expected' if expected else 'not selected',
                'test_outcome': outcomes.get(suite) if expected else None,
                'failed_phase': 'setup' if metadata.get('setup_failed') is True else None,
                'junit': junit if expected else None, 'artifact_name': artifact,
            })
    prefix = f'test-junit-{attempt}-'
    for artifact in sorted(directory.glob(f'{prefix}*')):
        if not artifact.is_dir() or artifact.is_symlink():
            continue
        for path in sorted(artifact.rglob('*.xml')):
            relative = path.relative_to(directory).as_posix()
            if relative not in claimed and path.is_file():
                rows.append({
                    'suite': path.relative_to(artifact).with_suffix('').as_posix(),
                    'platform': 'unknown', 'shard': artifact.name.removeprefix(prefix),
                    'job_outcome': 'unknown', 'expectation': 'observed',
                    'junit': relative, 'artifact_name': artifact.name,
                })
    return {'commit_sha': commit_sha, 'run_id': run_id, 'attempt': attempt,
            'matrix_outcome': matrix_outcome, 'download_outcome': download_outcome, 'invocations': rows}


def attach_github_observations(evidence, jobs, artifacts, run_url):
    """Describe native observations without promoting absent observations to success."""
    evidence['run_url'] = run_url
    evidence['observed_jobs'] = [
        {'name': job['name'], 'status': job['status'], 'conclusion': job['conclusion'],
         'url': job.get('html_url') or f"{run_url}/job/{job['id']}",
         'steps': [{key: step[key] for key in ('name', 'status', 'conclusion')}
                   for step in job.get('steps', [])]}
        for job in jobs if type(job.get('id')) is int
    ]
    by_name = {}
    for job in evidence['observed_jobs']:
        by_name.setdefault(job['name'], []).append(job)
    artifact_ids = {item['name']: item['id'] for item in artifacts if type(item.get('id')) is int}
    for row in evidence['invocations']:
        matches = by_name.get(f"backend-tests-windows-shard ({row['shard']})", []) if row['platform'] == 'Windows' else []
        # A skipped matrix may have only a parent placeholder, not one job per shard.
        if len(matches) == 1:
            job, = matches
            row['job_url'] = job['url']
            row['job_outcome'] = job['conclusion'] or 'unknown'
        name = row['artifact_name']
        if name in artifact_ids:
            row['artifact_url'] = f'{run_url}/artifacts/{artifact_ids[name]}'


def github_pages(endpoint, key):
    try:
        result = subprocess.run(
            ['gh', 'api', '--paginate', endpoint, '--jq', f'.{key}[] | @json'],
            check=True, capture_output=True, text=True, timeout=30,
        )
        return [json.loads(line) for line in result.stdout.splitlines()]
    except (OSError, ValueError, subprocess.SubprocessError):
        print(f'::warning::GitHub {key} metadata unavailable; reporting from available evidence')
        return None


def read_expected_plan(directory, attempt):
    path = directory / f'test-plan-{attempt}-windows' / 'windows-plan.json'
    if path.resolve().is_relative_to(directory.resolve()):
        plan = read_metadata(path)
        if all(NAME.fullmatch(shard) and isinstance(suites, dict) and all(
            NAME.fullmatch(suite) and isinstance(spec, dict) and type(spec.get('expected')) is bool
            for suite, spec in suites.items()
        ) for shard, suites in plan.items()):
            return plan
    return {}



def collect_run_evidence(directory, *, attempt, commit_sha, run_id, download_outcome, job_pages):
    """Keep the latest execution per job, including jobs untouched by a partial rerun."""
    latest = {}
    for number, jobs in job_pages.items():
        for job in jobs or ():
            latest[job['name']] = (number, job)
    complete = all(jobs is not None for jobs in job_pages.values())
    plan = next((plan for number in range(attempt, 0, -1)
                 if (plan := read_expected_plan(directory, number))), {})
    result = {'commit_sha': commit_sha, 'run_id': run_id, 'attempt': attempt,
              'matrix_outcome': None, 'download_outcome': download_outcome, 'invocations': []}
    for number in range(1, attempt + 1) if complete else (attempt,):
        snapshot = build_evidence(plan, directory, commit_sha=commit_sha, run_id=run_id,
                                  attempt=number, matrix_outcome=None, download_outcome=download_outcome)
        for row in snapshot['invocations']:
            name = (f"backend-tests-windows-shard ({row['shard']})"
                    if row['platform'] == 'Windows' else row['shard'])
            selected, job = latest.get(name, (attempt, None))
            if selected != number and complete:
                continue
            row['source_attempt'] = number
            if job:
                row['job_name'] = name
                row['job_outcome'] = job['conclusion'] or 'unknown'
                row['job_url'] = job.get('html_url')
            result['invocations'].append(row)
    return result, [job for _, job in latest.values()]


def main(mode):
    if mode == 'plan':
        config = json.loads(CONFIG.read_text(encoding='utf-8'), object_pairs_hook=unique_object)
        plan, matrix = make_plan(config, json.loads(os.environ['CHANGES']), os.environ['RUN_ALL'] == 'true')
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
            for key, value in {'plan': plan, 'matrix': matrix, 'selected': bool(matrix['include'])}.items():
                print(f'{key}={json.dumps(value, separators=(",", ":"))}', file=output)
    elif mode == 'publish-plan':
        plan = json.loads(os.environ['WINDOWS_PLAN'])
        expected = {shard: {suite: {'expected': spec['expected']} for suite, spec in suites.items()}
                    for shard, suites in plan.items()}
        Path('windows-plan.json').write_text(json.dumps(expected), encoding='utf-8')
    elif mode == 'run':
        plan = json.loads(os.environ['WINDOWS_PLAN'])
        return run_suites(plan[os.environ['WINDOWS_SHARD']], REPORTS)
    elif mode == 'record':
        dependency = os.environ['DEPENDENCY_OUTCOME']
        test = os.environ['TEST_OUTCOME']
        setup_failed = dependency != 'success' and test == 'skipped'
        outcome = 'failure' if setup_failed else test
        REPORTS.mkdir(exist_ok=True)
        (REPORTS / 'shard.json').write_text(json.dumps({'outcome': outcome, 'setup_failed': setup_failed}), encoding='utf-8')
    elif mode == 'report':
        attempt = int(os.environ['SOURCE_ATTEMPT'])
        repository = os.environ['GITHUB_REPOSITORY']
        run_id = os.environ['SOURCE_RUN_ID']
        run_url = f"{os.environ['GITHUB_SERVER_URL']}/{repository}/actions/runs/{run_id}"
        endpoint = f"repos/{repository}/actions/runs/{run_id}"
        job_pages = {number: github_pages(f'{endpoint}/attempts/{number}/jobs?per_page=100', 'jobs')
                     for number in range(1, attempt + 1)}
        evidence, jobs = collect_run_evidence(
            EVIDENCE, attempt=attempt, commit_sha=os.environ['SOURCE_SHA'], run_id=run_id,
            download_outcome=os.environ['DOWNLOAD_OUTCOME'], job_pages=job_pages,
        )
        evidence['source_conclusion'] = os.environ['SOURCE_CONCLUSION']
        artifacts = github_pages(f'{endpoint}/artifacts?per_page=100', 'artifacts')
        attach_github_observations(evidence, jobs or [], artifacts or [], run_url)
        reported_jobs = {row.get('job_name') for row in evidence['invocations'] if row.get('junit')}
        for job in evidence['observed_jobs']:
            job['has_junit_candidate'] = job['name'] in reported_jobs
        evidence['github_metadata'] = {'jobs_available': all(page is not None for page in job_pages.values()), 'artifacts_available': artifacts is not None}
        EVIDENCE.mkdir(exist_ok=True)
        (EVIDENCE / 'evidence.json').write_text(json.dumps(evidence), encoding='utf-8')
    else:
        raise ValueError(f'Unknown command: {mode}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1]))
