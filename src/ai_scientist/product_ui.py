"""Navigation and artifact-first presentation, independent of scientific judgments."""
import base64
import hashlib
from pathlib import PurePosixPath
from urllib.parse import quote

import streamlit as st

from src.ai_scientist.figure_refs import answer_blocks, clean_figure_refs, computation_attempts, figures_for_audit


def has_inline_figures(messages, attempt):
    expected = {f['figure_id']: f for f in figures_for_audit(attempt or {})}
    for message in messages:
        bound = {f['figure_id'] for f in clean_figure_refs(message.get('figures', []))
                 if expected.get(f['figure_id']) == f}
        if any(kind == 'figure' and content in bound
               for kind, content in answer_blocks(message.get('public_response') or '')):
            return True
    return False


def render_model_answer(backend_url, project, text, figures, get_json, key_prefix):
    """Render model-authored placement against this project's immutable manifest."""
    allowed = {f['figure_id']: f for a in computation_attempts(project) for f in figures_for_audit(a)}
    bound = {f['figure_id']: f for f in clean_figure_refs(figures)
             if allowed.get(f['figure_id']) == f}
    for index, (kind, content) in enumerate(answer_blocks(text)):
        if kind == 'text':
            st.markdown(content, unsafe_allow_html=False)
            continue
        figure = bound.get(content)
        if figure is None or get_json is None:
            st.caption('此处引用的图表尚不可用，未替换为其他实验的图片。')
            continue
        path = figure['relative_path']
        try:
            cache_key = ('verified_figure', backend_url, project['project_id'], content)
            if cache_key not in st.session_state:
                payload = get_json(backend_url, f"/api/research/{project['project_id']}/computation/file?path={quote(path, safe='')}")
                data = base64.b64decode(payload['content_base64'], validate=True)
                if hashlib.sha256(data).hexdigest() != figure['checksum_sha256']:
                    raise ValueError('Figure checksum mismatch')
                st.session_state[cache_key] = data
            data = st.session_state[cache_key]
            st.image(data, caption=PurePosixPath(path).name, use_container_width=True)
            st.download_button('下载此图', data, file_name=PurePosixPath(path).name,
                               key=f'{key_prefix}_figure_{index}_{content}')
        except Exception:
            st.warning('此处图表无法读取或校验未通过，回答正文仍保留。请稍后重试或查看“资料与成果”。')


def render_project_sidebar(backend_url, get_json, refresh_project, phase_labels, rename_project=None):
    remembered = st.query_params.get('project')
    if remembered and not st.session_state.get('research_project_id'):
        if remembered.startswith('project_') and remembered.replace('_', '').isalnum():
            try:
                st.session_state.research_project_id = remembered
                refresh_project(backend_url)
            except Exception:
                st.session_state.research_project_id = None
    with st.sidebar:
        if st.button('＋ 新研究', use_container_width=True):
            for key in ('research_project', 'research_project_id', 'research_job_id'):
                st.session_state[key] = None
            st.session_state.research_objective = ''
            st.session_state.research_constraints_text = ''
            st.session_state.research_example_case = ''
            st.query_params.clear()
            st.rerun()
        query = st.text_input('搜索研究', placeholder='按项目名称搜索')
        try:
            projects = get_json(backend_url, '/api/research-projects')
        except Exception:
            st.caption('项目列表暂不可用；不会清除当前研究。')
            return
        if not isinstance(projects, list):
            return
        matches = [p for p in projects if query.casefold() in p.get('title', '').casefold()]
        st.caption(f'历史研究 · {len(matches)}')
        with st.container(height=550):
            for p in matches:
                pid = p['project_id']
                selected = pid == st.session_state.get('research_project_id')
                title = p.get('title') or '未命名研究'
                label = title if len(title) <= 48 else title[:48] + '…'
                if st.button(label, key='open_' + pid, use_container_width=True,
                             type='primary' if selected else 'secondary',
                             help=f"{title} · {phase_labels.get(p.get('phase'), p.get('phase'))} · {p.get('updated_at', '')[:10]} · {pid}"):
                    old = (st.session_state.get('research_project_id'), st.session_state.get('research_project'))
                    try:
                        st.session_state.research_project_id = pid
                        st.session_state.research_job_id = None
                        refresh_project(backend_url)
                    except Exception:
                        st.session_state.research_project_id, st.session_state.research_project = old
                        st.error('无法加载此研究，已保留当前页面。')
                    else:
                        st.query_params['project'] = pid
                        st.rerun()
                if selected and rename_project is not None:
                    with st.expander('✎ 重命名', expanded=False):
                        with st.form('sidebar_rename_' + pid):
                            name = st.text_input('研究名称', value=title, max_chars=160,
                                                 key=f'sidebar_name_{pid}_{title}')
                            st.caption('只修改显示名称，不改变研究问题、记录或结果。运行中的研究请暂停并等待当前任务结束后改名。')
                            submitted = st.form_submit_button('保存名称')
                        if submitted:
                            if not name.strip():
                                st.warning('研究名称不能为空。')
                            else:
                                try:
                                    rename_project(backend_url, f'/api/research/{pid}/title',
                                                   {'title': name.strip()})
                                except Exception as exc:
                                    if getattr(exc, 'status_code', None) == 409:
                                        st.warning('研究正在运行，请暂停并等待当前任务结束后再改名。')
                                    else:
                                        st.error('名称未保存，请检查服务连接后重试。原名称和研究记录不会被清除。')


def latest_successful_attempt(project):
    state = project.get('computation') or {}
    attempts = list(reversed(state.get('attempts', [])))
    for record in reversed(state.get('rounds', [])):
        attempts.extend(reversed(record.get('attempts', [])))
    return next((a for a in attempts if a.get('status') == 'success'), None)


def render_result_gallery(backend_url, project, get_json):
    """Show actual saved images immediately, even while assessment is running."""
    attempt = latest_successful_attempt(project)
    if not attempt:
        return
    images = figures_for_audit(attempt)
    if not images:
        return
    st.subheader('已生成的图表')
    st.caption('来自实际计算文件；计算成功不等于科学结论已通过复审。')
    columns = st.columns(2)
    for index, item in enumerate(images[:8]):
        with columns[index % 2]:
            render_model_answer(backend_url, project, item['marker'], images, get_json,
                                f'gallery_{project["project_id"]}_{index}')
