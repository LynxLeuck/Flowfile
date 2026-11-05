"""Diagnostic only: repeated real-service runs, no worker mocks."""
import json
import os
from pathlib import Path

import polars as pl
import pytest
from test_utils.services import running_services, flow_document, failed_nodes


@pytest.mark.parametrize('own_storage', [False, True])
def test_repeated_explore(tmp_path, own_storage):
    evidence = Path(os.environ['ISSUE878_EVIDENCE']) / ('separate' if own_storage else 'shared')
    evidence.mkdir(parents=True, exist_ok=True)
    source = tmp_path / 'source.csv'
    pl.DataFrame({'id': list(range(100)), 'amount': list(range(100))}).write_csv(source)
    nodes = []
    for branch in range(3):
        n = branch * 4 + 1
        nodes.extend([
            {'id': n, 'type': 'read', 'is_start_node': True, 'outputs': [n+1],
             'setting_input': {'received_file': {'name': source.name, 'path': str(source), 'file_type': 'csv',
                'table_settings': {'file_type': 'csv', 'delimiter': ',', 'has_headers': True, 'encoding': 'utf-8'}}}},
            {'id': n+1, 'type': 'filter', 'input_ids': [n], 'outputs': [n+2],
             'setting_input': {'filter_input': {'mode': 'advanced', 'advanced_filter': '[amount] >= 10'}}},
            {'id': n+2, 'type': 'filter', 'input_ids': [n+1], 'outputs': [n+3],
             'setting_input': {'filter_input': {'mode': 'advanced', 'advanced_filter': '[amount] < 90'}}},
            {'id': n+3, 'type': 'explore_data', 'input_ids': [n+2], 'setting_input': {}},
        ])
    reports = []
    try:
        with running_services(tmp_path, 'electron', {'FLOWFILE_COMPUTE_PROTOCOL': '1',
                              'FLOWFILE_WORKER_LOCAL_FILES': '1'}, own_storage=own_storage) as services:
            flow_id = services.import_flow(flow_document(8781, 'issue878 repeated explore', nodes))
            for iteration in range(12):
                run = services.run(flow_id)
                reports.append(run)
                (evidence / 'runs.json').write_text(json.dumps(reports, indent=2))
                errors = failed_nodes(run)
                print(json.dumps({'iteration': iteration+1, 'success': run['success'], 'errors': errors}), flush=True)
                assert run['success'] is True and not errors, errors
                assert len(run['node_step_result']) == 12, run
                for node_id in (4, 8, 12):
                    preview = services.get('/node/data', flow_id=flow_id, node_id=node_id)
                    assert preview['number_of_records'] == 80, preview
                    assert all(10 <= row['amount'] < 90 for row in preview['data']), preview
    finally:
        for name in ('core.log', 'worker.log'):
            log = tmp_path / name
            if log.exists():
                (evidence / name).write_bytes(log.read_bytes())
