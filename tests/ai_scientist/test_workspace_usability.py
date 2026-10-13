"""User-facing ordering, attachment retry and narrowly scoped real deletion."""
import io
import pytest
from streamlit.testing.v1 import AppTest
from tests.test_conversation_api import api
from src.ai_scientist.research_timeline import timeline_entries, timeline_partition
from src.ai_scientist.composer_delivery import save_attachments_once


def test_timeline_orders_offsets_and_keeps_reply_relation():
    p = {'human_dialogue': {'messages': [{'id': 'u', 'text': 'Question',
          'created_at': '2026-10-13T10:00:00+08:00', 'response': 'Saved reply',
          'answered_at': '2026-10-13T02:02:00Z'}]}}
    rows = timeline_entries(p, [{'public_response': 'Earlier', 'timestamp': '2026-10-13T01:59:00Z'}])
    assert [r['kind'] for r in rows] == ['model', 'user', 'model']
    assert rows[-1]['data']['dialogue_message_id'] == 'u'
    old, current = timeline_partition([{}] * 12)
    assert old + current == list(range(12))
    assert max(old) < min(current)


def test_record_start_time_does_not_move_during_stream(api):
    j = api.jobs.create(api.project.project_id, 'BACKGROUND_RESEARCH')
    for status, time in [('started', '01'), ('completed', '09')]:
        api.jobs.record_model_progress(api.project.project_id, j.job_id,
            {'call_id': 'call', 'status': status, 'timestamp': f'2026-10-13T02:00:{time}Z'})
    row = api.jobs.load(api.project.project_id, j.job_id).model_updates[0]
    assert row['started_at'].endswith('01Z') and row['timestamp'].endswith('09Z')


def test_upload_retry_only_retries_failed_file():
    files = [io.BytesIO(b'one'), io.BytesIO(b'two')]
    for n, f in enumerate(files):
        f.name = f'{n}.txt'
    cache, calls = {}, []
    def save(f):
        calls.append(f.name)
        if f.name == '1.txt' and calls.count(f.name) == 1:
            return [], ['Simulated network failure']
        return [{'asset_id': f.name}], []
    saved, errors = save_attachments_once(files, {'kind': 'reference'}, '', cache, save)
    assert len(saved) == 1 and errors
    saved, errors = save_attachments_once(files, {'kind': 'reference'}, '', cache, save)
    assert len(saved) == 2 and not errors and calls == ['0.txt', '1.txt', '1.txt']


def test_foreign_attachment_rejected_before_model_job(api):
    r = api.client.post(f'/api/research/{api.project.project_id}/message', json={
        'message': 'Read this', 'message_id': 'upload1', 'attachment_ids': ['foreign']})
    assert r.status_code == 422 and not api.threads


def delete_params(api):
    pid = api.project.project_id
    preview = api.client.get(f'/api/research/{pid}/deletion-preview').json()
    return {'confirm_project_id': pid, 'confirmation_token': preview['confirmation_token'], 'acknowledged': True}


def test_deletion_removes_actual_project_only(api):
    p = api.project
    other = api.o.create_project('Keep me')
    root = api.o.store.project_dir(p.project_id)
    (root / 'uploads').mkdir()
    (root / 'uploads' / 'input.txt').write_text('fixture')
    r = api.client.delete(f'/api/research/{p.project_id}', params=delete_params(api))
    assert r.status_code == 200 and r.json()['deleted']
    assert not root.exists()
    assert api.o.store.project_dir(other.project_id).exists()
    assert all(row['project_id'] != p.project_id for row in api.o.store.list_projects())


def test_delete_requires_confirmation_and_fresh_snapshot(api):
    pid = api.project.project_id
    params = delete_params(api)
    r = api.client.delete(f'/api/research/{pid}', params={**params, 'acknowledged': False})
    assert r.status_code == 422
    api.o.store.save(api.project)
    assert api.client.delete(f'/api/research/{pid}', params=params).status_code >= 400
    assert api.o.store.project_dir(pid).exists()


def test_running_project_cannot_be_deleted(api):
    params = delete_params(api)
    api.jobs.create(api.project.project_id, 'BACKGROUND_RESEARCH')
    r = api.client.delete(f'/api/research/{api.project.project_id}', params=params)
    assert r.status_code == 409
    assert api.o.store.project_dir(api.project.project_id).exists()


def test_delete_refuses_symlinks_and_path_traversal(api, tmp_path):
    for pid in ['..', '.', '/tmp', 'project_../other']:
        with pytest.raises(ValueError):
            api.o.store.deletion_preview(pid)
    target = tmp_path / 'external.txt'
    target.write_text('Do not delete')
    root = api.o.store.project_dir(api.project.project_id)
    (root / 'linked').symlink_to(target)
    with pytest.raises(ValueError):
        api.o.store.deletion_preview(api.project.project_id)
    assert target.exists() and root.exists()


def test_composer_has_one_message_field_and_keeps_busy_draft():
    app = AppTest.from_string('''
from src.ai_scientist.research_timeline import render_composer
render_composer({'project_id':'p'}, True, lambda *a:None, lambda *a:None,
    save_files=lambda *a:([],[]))
''').run()
    assert not app.exception
    assert len(app.text_area) == 1 and not app.text_area[0].disabled
    assert next(b for b in app.button if b.label == '发送').disabled
    app.text_area[0].input('Keep my draft').run()
    assert app.text_area[0].value == 'Keep my draft'


def test_sidebar_delete_requires_preview_then_explicit_confirmation():
    app = AppTest.from_string('''
import streamlit as st
from src.ai_scientist.product_ui import render_project_sidebar
st.session_state.setdefault('research_project_id','project_test')
def get(url,path):
    if path.endswith('deletion-preview'):
        return {'title':'Test project','file_count':3,'size_bytes':100,'confirmation_token':'token','running':False}
    return [{'project_id':'project_test','title':'Test project'}]
def delete(url,path):
    st.session_state['deleted_path'] = path
render_project_sidebar('',get,lambda _:None,{},delete_project=delete)
''').run()
    assert not any(b.label == '永久删除项目' for b in app.button)
    next(b for b in app.button if b.label == '检查删除范围').click().run()
    assert next(b for b in app.button if b.label == '永久删除项目').disabled
    next(c for c in app.checkbox if c.label == '我确认永久删除这个项目及其文件').check().run()
    next(b for b in app.button if b.label == '永久删除项目').click().run()
    assert not app.exception
    assert 'confirmation_token=token' in app.session_state['deleted_path']
    assert app.session_state['research_project_id'] is None
