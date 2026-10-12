"""One visible research answer, with optional review rather than a publication wall."""
import streamlit as st
import hashlib
from contextlib import nullcontext
from src.ai_scientist.literature_experience import current_draft, export_report


def render_literature_answer(project, running=False, submit=None):
    state = project.get('literature_review') or {}
    text = current_draft(state)
    if not text:
        return
    st.markdown('<div id="research-answer"></div>', unsafe_allow_html=True)
    if state.get('generation_error'):
        st.warning(state['generation_error'])
    revising = running and state.get('status') == 'synthesizing'
    if revising:
        st.caption('正在按你的意见更新回答；上一版保留在下方，新正文会在研究对话中逐步出现。')
    else:
        st.subheader('研究回答')
    labels = {'checked': '已做一次模型辅助核查', 'issues_found': '有待核对意见',
              'unavailable': '补充核查未完成', 'not_requested': '尚未补充核查', 'running': '正在补充核查'}
    status = labels.get(state.get('review_status'), '已有草稿，最终核查未完成')
    if not running and state.get('review_status') == 'running':
        status = '补充核查已中断，草稿保留'
    with st.expander('上一版回答（修改中，尚未替换）', expanded=False) if revising else nullcontext():
        st.caption(status + ' · 回答可直接阅读；工作流结束不代表科学结论已证实。')
        st.markdown(text, unsafe_allow_html=False)
    st.download_button('下载研究回答（含核查状态）', export_report(state),
                       file_name='research_answer.md', mime='text/markdown', key='research_answer_download')
    notes = state.get('review_notes') or []
    old_error = state.get('last_error') or {}
    if not notes and isinstance(old_error, dict) and old_error.get('rationale'):
        notes = [old_error['rationale']]
    if notes:
        with st.expander('核查意见与未解决问题', expanded=state.get('review_status') in {'issues_found', 'unavailable'}):
            st.caption('以下是审核意见，不是新的原始证据。是否修改应结合原文判断。')
            for note in notes:
                st.markdown(note, unsafe_allow_html=False)
    if submit and not running:
        render_draft_actions(project, submit)
    if state.get('draft_history'):
        with st.expander('查看之前的回答版本'):
            history = state['draft_history']
            version = st.selectbox('历史版本', range(len(history)),
                format_func=lambda i: f'第 {i + 1} 版', key=f"draft_history_{project['project_id']}")
            st.markdown(history[version]['report_markdown'])
            st.caption('历史版本仅供比较，不是当前交付。')


def render_draft_actions(project, submit):
    pid = project['project_id']
    state = project.get('literature_review') or {}
    completed = project.get('phase') == 'COMPLETED'
    budget = project.get('budget') or {}
    exhausted = bool(budget.get('max_model_calls') is not None and
                     budget.get('attempted_model_calls', 0) >= budget['max_model_calls'])
    if exhausted:
        st.info('本轮模型调用额度已用完。回答和历史仍可阅读、下载或保存；继续修改或核查需要追加额度。')
        current_limit = max(budget['max_model_calls'], budget.get('attempted_model_calls', 0))
        if current_limit < 300:
            with st.expander('追加模型调用额度'):
                limit = st.number_input('模型请求总上限', min_value=current_limit + 1,
                    max_value=300, value=min(current_limit + 10, 300), key=f'literature_budget_{pid}')
                accepted = st.checkbox('我同意增加可能产生的 API 费用；不会自动开始调用。', key=f'budget_ack_{pid}')
                if st.button('确认追加额度', disabled=not accepted, key=f'budget_submit_{pid}'):
                    submit({'action': 'budget', 'max_model_calls': int(limit), 'acknowledged': True})
    if completed:
        st.caption('本轮已保存。继续修改或核查将重新打开本轮，保留上一份交付，不重新检索或批准实验。')
    if state.get('review_status') == 'issues_found' and state.get('review_notes'):
        if st.button('根据核查意见修改回答', key=f'apply_review_{pid}', type='primary', disabled=exhausted):
            submit({'action': 'revise', 'feedback': '请结合实际原文核对并处理以下意见；不要盲从审核，不新增检索。'
                    '\n' + '\n'.join(state['review_notes'])})
    if st.button('补充核查回答', key=f'review_draft_{pid}', disabled=exhausted):
        submit({'action': 'review'})
    with st.expander('修改这份回答'):
        with st.form(f'revise_draft_{pid}'):
            feedback = st.text_area('你希望修改或补充什么？')
            if st.form_submit_button('提交意见并修改回答', disabled=exhausted):
                if feedback.strip():
                    submit({'action': 'revise', 'feedback': feedback.strip()})
                else:
                    st.warning('请先填写修改意见。')
    if completed:
        return
    version = hashlib.sha256((current_draft(state) + str(state.get('review_status'))).encode()).hexdigest()[:12]
    acknowledged = st.checkbox('保留当前核查状态结束本轮；不代表科学结论已经验证。', key=f'finish_ack_{pid}_{version}')
    if st.button('保存回答并结束本轮', type='primary', disabled=not acknowledged, key=f'finish_draft_{pid}'):
        submit({'action': 'finish', 'acknowledged': True})
