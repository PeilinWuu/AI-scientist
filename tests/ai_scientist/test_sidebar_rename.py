"""Sidebar interaction tests; no live services or model calls."""
from streamlit.testing.v1 import AppTest


APP = '''
import streamlit as st
from src.ai_scientist.product_ui import render_project_sidebar
if 'research_project_id' not in st.session_state:
    st.session_state.research_project_id = 'project_test'
    st.session_state.name = '原研究名称'
    st.session_state.calls = []
def get_json(*args):
    return [dict(project_id='project_test', title=st.session_state.name,
                 phase='COMPLETED', updated_at='2026-10-11')]
def rename(base, path, payload):
    if st.session_state.get('busy'):
        error = RuntimeError('conflict')
        error.status_code = 409
        raise error
    st.session_state.calls.append((path, payload))
    st.session_state.name = payload['title']
    st.rerun()
render_project_sidebar('unused', get_json, lambda _: None, {}, rename)
'''


def test_sidebar_rename_only_saves_on_submit_and_refreshes_name():
    app = AppTest.from_string(APP).run()
    field = next(w for w in app.text_input if w.label == '研究名称')
    field.input('  城市空气质量研究  ').run()
    assert app.session_state.calls == []
    next(w for w in app.button if w.label == '保存名称').click().run()
    assert not app.exception
    assert app.session_state.calls == [('/api/research/project_test/title', {'title': '城市空气质量研究'})]
    assert any(w.label == '城市空气质量研究' for w in app.button)
    assert app.session_state.research_project_id == 'project_test'


def test_sidebar_rejects_blank_name():
    app = AppTest.from_string(APP).run()
    next(w for w in app.text_input if w.label == '研究名称').input('   ')
    next(w for w in app.button if w.label == '保存名称').click().run()
    assert app.session_state.calls == []
    assert app.warning[0].value == '研究名称不能为空。'


def test_sidebar_explains_busy_project_without_clearing_name():
    app = AppTest.from_string(APP).run()
    app.session_state.busy = True
    next(w for w in app.text_input if w.label == '研究名称').input('新名称')
    next(w for w in app.button if w.label == '保存名称').click().run()
    assert not app.exception
    assert app.session_state.name == '原研究名称'
    assert app.session_state.calls == []
    assert '等待当前任务结束' in app.warning[0].value
