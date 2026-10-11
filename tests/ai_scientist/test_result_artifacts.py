import hashlib
import json

import pytest

from src.ai_scientist.result_artifacts import inspect_result_artifacts


def executed_artifacts(root, outputs=None):
    run = root / 'computation' / 'test_run'
    output_dir = run / 'output'
    output_dir.mkdir(parents=True)
    program = run / 'program.py'
    program.write_text('print("actual source")', encoding='utf-8')
    artifacts = []
    for name, content in (outputs or {'result.json': '{"metrics":{"value":1}}',
                                   'table.csv': 'n,coverage\n10,0.90\n30,0.95\n'}).items():
        path = output_dir / name
        path.write_bytes(content if isinstance(content, bytes) else content.encode())
        artifacts.append({'relative_path': str(path.relative_to(root)),
                          'checksum_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                          'size_bytes': path.stat().st_size})
    return {'run_id': 'test_run', 'code_path': str(program.relative_to(root)),
            'code_sha256': hashlib.sha256(program.read_bytes()).hexdigest(), 'artifacts': artifacts}


def test_verified_csv_cells_and_actual_program_reach_assessment(tmp_path):
    audit = executed_artifacts(tmp_path)
    result = inspect_result_artifacts(tmp_path, audit, ['result.json', 'table.csv'])
    assert result['delivery_validation']['status'] == 'complete'
    table = next(item for item in result['artifacts'] if item['relative_path'].endswith('table.csv'))
    assert json.loads(table['preview'])['rows'] == [['10', '0.90'], ['30', '0.95']]
    assert table['integrity_verified']
    assert table['trust'] == 'untrusted_research_data'
    assert any(item['role'] == 'executed_program' for item in result['artifacts'])
    assert not result['delivery_validation']['scientific_validity_checked']


@pytest.mark.parametrize('attack', ['modified', 'symlink', 'traversal', 'different_run'])
def test_modified_or_unsafe_artifacts_are_not_read(tmp_path, attack):
    audit = executed_artifacts(tmp_path)
    table = audit['artifacts'][1]
    path = tmp_path / table['relative_path']
    if attack == 'modified':
        path.write_text('n,coverage\n10,0.99\n30,0.95\n')
    elif attack == 'symlink':
        source = tmp_path / 'private.txt'
        source.write_text('DO NOT READ THIS SECRET')
        path.unlink()
        path.symlink_to(source)
    elif attack == 'traversal':
        table['relative_path'] = 'computation/test_run/output/../../../private.txt'
    else:
        table['relative_path'] = 'computation/other_run/output/table.csv'
    result = inspect_result_artifacts(tmp_path, audit, ['result.json', 'table.csv'])
    assert result['delivery_validation']['status'] == 'needs_revision'
    assert 'DO NOT READ THIS SECRET' not in json.dumps(result)
    assert not result['artifacts'][1].get('preview')


def test_previews_bounded_and_omissions_explicit(tmp_path):
    audit = executed_artifacts(tmp_path, {'result.json': '{"metrics":{"value":1}}',
                                        'large.csv': 'number,text\n' + '1,some data\n' * 10000})
    result = inspect_result_artifacts(tmp_path, audit, ['large.csv'])
    table = result['artifacts'][1]
    assert table['truncated']
    assert len(json.loads(table['preview'])['rows']) == 120
    assert len(json.dumps(result)) < 10000


def test_oversized_csv_header_returns_readable_diagnostic(tmp_path):
    audit = executed_artifacts(tmp_path, {'result.json': '{"metrics":{"value":1}}',
                                        'huge_header.csv': 'column' * 12000 + '\n1\n'})
    result = inspect_result_artifacts(tmp_path, audit, ['huge_header.csv'])
    assert result['delivery_validation']['status'] == 'needs_revision'
    assert 'header exceeds' in result['artifacts'][1]['error']


def test_missing_invalid_text_or_png_reported(tmp_path):
    audit = executed_artifacts(tmp_path, {'result.json': '{"metrics":{"value":1}}',
                                        'bad.txt': b'\xff\xfe', 'fake.png': b'not a PNG'})
    result = inspect_result_artifacts(tmp_path, audit, ['bad.txt', 'fake.png', 'missing.csv'])
    issues = result['delivery_validation']['issues']
    assert any('missing.csv' in issue for issue in issues)
    assert any('bad.txt' in issue for issue in issues)
    assert any('Invalid PNG' in issue for issue in issues)


def test_png_read_not_misrepresented_as_visual_inspection(tmp_path):
    audit = executed_artifacts(tmp_path, {'result.json': '{"metrics":{"value":1}}',
                                        'figure.png': b'\x89PNG\r\n\x1a\nimage bytes'})
    result = inspect_result_artifacts(tmp_path, audit, ['figure.png'])
    assert result['delivery_validation']['visual_review_pending'] == ['computation/test_run/output/figure.png']
    assert 'NOT been visually inspected' in result['artifacts'][1]['description']
