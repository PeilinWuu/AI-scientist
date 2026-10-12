"""Figure attribution, model placement, and UI rendering without live model calls."""
import base64
import hashlib
import io
from types import SimpleNamespace

from PIL import Image
from streamlit.testing.v1 import AppTest

from src.ai_scientist.figure_refs import (
    answer_blocks, available_figures, clean_figure_refs, export_figure_markdown, figures_for_audit,
)
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.product_ui import has_inline_figures
from src.ai_scientist.model_progress import model_progress
from src.ai_scientist.structured_client import StructuredQwenClient, StructuredCallMetadata
from src.ai_scientist.schemas import utc_now


def fixture_data(run='run_a'):
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(buffer, format='PNG')
    data = buffer.getvalue()
    audit = {'run_id': run, 'status': 'success', 'artifacts': [
        {'relative_path': f'computation/{run}/output/plot.png',
         'checksum_sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)}]}
    return data, audit, figures_for_audit(audit)[0]


def test_refs_are_bound_to_exact_run_and_strip_unknown_fields():
    _, _, figure = fixture_data()
    assert clean_figure_refs([{**figure, 'reasoning_content': 'PRIVATE'}]) == [figure]
    assert clean_figure_refs([{**figure, 'run_id': 'other_run'}]) == []
    assert clean_figure_refs([{**figure, 'relative_path': '/etc/secret.png'}]) == []
    assert clean_figure_refs([{**figure, 'relative_path': 'computation/run_a/output/../plot.png'}]) == []
    assert fixture_data('run_b')[2]['figure_id'] != figure['figure_id']


def test_only_verified_latest_success_is_offered_no_silent_older_fallback(tmp_path):
    data, audit, figure = fixture_data()
    target = tmp_path / figure['relative_path']
    target.parent.mkdir(parents=True)
    target.write_bytes(data)
    project = {'computation': {'attempts': [audit]}}
    assert available_figures(project, tmp_path) == [figure]
    target.write_bytes(b'changed')
    assert available_figures(project, tmp_path) == []
    target.unlink()
    real = tmp_path / 'actual.png'
    real.write_bytes(data)
    target.symlink_to(real)
    assert available_figures(project, tmp_path) == []


def test_answer_order_code_and_export():
    _, _, figure = fixture_data()
    text = f"结果说明。\n\n{figure['marker']}\n\n这不代表因果。\n"
    assert list(answer_blocks(text)) == [('text', '结果说明。\n\n'), ('figure', figure['figure_id']),
                                         ('text', '\n这不代表因果。\n')]
    code = f"```text\n{figure['marker']}\n```\n"
    assert list(answer_blocks(code)) == [('text', code)]
    exported = export_figure_markdown(text, [figure])
    assert f"(<../{figure['relative_path']}>)" in exported
    assert '[[figure:' not in exported
    assert export_figure_markdown('[[figure:unknown]]', []) == '> 图表引用不可用。\n'


def test_available_but_unused_figures_do_not_hide_uninterpreted_results():
    _, audit, figure = fixture_data()
    message = {'figures': [figure], 'public_response': '尚未解读图表。'}
    assert not has_inline_figures([message], audit)
    message['public_response'] = figure['marker']
    assert has_inline_figures([message], audit)
    assert not has_inline_figures([message], fixture_data('run_b')[1])


def test_model_receives_markers_and_progress_persists_exact_manifest(tmp_path, monkeypatch):
    _, _, figure = fixture_data()
    client = StructuredQwenClient()
    client.public_figures = [figure]
    seen = []
    def call(agent, instructions, payload, schema):
        seen.append((instructions, payload))
        return SimpleNamespace(value=None, metadata=StructuredCallMetadata(
            agent_name=agent, requested_model='offline', actual_model='offline', fallback_used=False,
            started_at=utc_now(), finished_at=utc_now(), public_response=figure['marker']))
    monkeypatch.setattr(client, '_call', call)
    store = ResearchJobStore(tmp_path)
    job = store.create('project_test', 'ANALYSIS')
    with model_progress(lambda event: store.record_model_progress(job.project_id, job.job_id, event)):
        result = client.call('analyst', 'Study results.', {}, dict)
    assert 'between the relevant paragraphs' in seen[0][0]
    assert seen[0][1]['available_figures'] == [figure]
    assert result.metadata.figures == [figure]
    saved = store.load(job.project_id, job.job_id).model_updates[0]
    assert saved['figures'] == [figure]
    assert saved['public_response'] == figure['marker']


def render_app(project, figures, text, data):
    source = '''
import base64, json
import streamlit as st
from src.ai_scientist.product_ui import render_model_answer
if 'fetches' not in st.session_state:
    st.session_state.fetches = []
def get_json(base, path):
    st.session_state.fetches.append(path)
    return {'content_base64': DATA}
render_model_answer('test', PROJECT, TEXT, FIGURES, get_json, 'test')
'''
    prefix = f'PROJECT = {project!r}\nFIGURES = {figures!r}\nTEXT = {text!r}\nDATA = {base64.b64encode(data).decode()!r}\n'
    return AppTest.from_string(prefix + source).run()


def test_ui_renders_figure_between_prose_and_does_not_rewrite_answer():
    data, audit, figure = fixture_data()
    text = f"前面的模型解释。\n\n{figure['marker']}\n\n后面的模型解释。"
    project = {'project_id': 'project_test', 'computation': {'attempts': [audit]}}
    app = render_app(project, [figure], text, data)
    assert not app.exception
    assert [m.value.strip() for m in app.markdown] == ['前面的模型解释。', '后面的模型解释。']
    assert len(app.get('image')) == 1
    types = [node.type for node in app.main]
    assert types.index('markdown') < types.index('image') < len(types) - 1
    assert types[-1] == 'markdown'
    assert len(app.session_state.fetches) == 1


def test_ui_rejects_cross_run_binding_and_modified_bytes():
    data, audit, figure = fixture_data()
    project = {'project_id': 'project_test', 'computation': {'attempts': [audit]}}
    other = fixture_data('run_b')[2]
    app = render_app(project, [other], other['marker'], data)
    assert not app.exception and not app.session_state.fetches
    assert len(app.get('image')) == 0
    app = render_app(project, [figure], figure['marker'], b'changed')
    assert not app.exception and len(app.get('image')) == 0
    assert '校验未通过' in app.warning[0].value
