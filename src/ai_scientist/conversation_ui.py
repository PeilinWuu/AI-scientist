"""Public Qwen messages, distinct from tool facts and private model reasoning."""
import streamlit as st
from src.ai_scientist.product_ui import render_model_answer


def render_research_conversation(project, messages, phase_labels, backend_url='', get_json=None):
    st.subheader('与 Qwen 一起研究')
    st.caption('Qwen 的研究回答与实际工具进展。历史过程和调用信息可按需展开。')
    if project.get('revision_feedback'):
        with st.expander('你最近的修订意见 · 已保存', expanded=False):
            st.write(project['revision_feedback'][-1])
            st.caption('已保存不等于已落实。下方历史回复可能来自修订前版本，请核对后续修订结果再批准。')
    observations = (project.get('background_agent') or {}).get('observations', [])
    if observations:
        with st.expander('实际工具进展', expanded=False):
            for observation in observations[-8:]:
                status = {'completed': '已返回', 'failed': '未完成'}.get(observation.get('status'), '尚无完成记录')
                result = observation.get('result') or {}
                if result.get('status') in {'proposal_requires_revision', 'tool_not_executed'}:
                    status = '尚未完成'
                st.write(f"{status} · {observation.get('purpose') or observation.get('action', '工具操作')}")
            st.caption('这里只显示实际工具记录，不将行动计划或模型推测当作已完成的操作。')
    if not messages:
        st.info('Qwen 的回答会显示在这里。请在顶部开始或继续研究；历史研究产物可在“资料与成果”中查看。')
    show_history = st.checkbox('展开全部阶段记录', value=False, key=f"all_messages_{project['project_id']}")
    has_answer = bool(((project.get('literature_review') or {}).get('draft') or {}).get('report_markdown'))
    has_reading_answer = bool((project.get('background_agent') or {}).get('partial_answer'))
    background_phases = {'BACKGROUND_RESEARCH', 'HUMAN_SOURCE_REVIEW', 'SEARCH_PLAN_REVIEW'}
    substantive = [m for m in messages if m.get('agent_name') != 'skeptical_reviewer'
                   and (not has_answer or m.get('status') not in {'completed', 'failure', 'failed'})
                   and (not has_reading_answer or m.get('phase') not in background_phases
                        or m.get('status') not in {'completed', 'failure', 'failed'})]
    if has_reading_answer and project.get('phase') in background_phases:
        substantive = [m for m in substantive if m.get('status') not in {'completed', 'failure', 'failed'}]
    visible = messages if show_history else substantive[-3:]
    if messages and not show_history:
        st.caption(f'已保留 {len(messages)} 条过程记录；审核与旧草稿收在“展开全部阶段记录”中。')
    for index, message in enumerate(visible):
        phase = phase_labels.get(message.get('phase'), message.get('phase', '研究'))
        label = f"{phase} · {message.get('model') or 'Qwen'}"
        proposal = message.get('message_kind') == 'action_proposal'
        draft = message.get('message_kind') == 'research_draft'
        status = message.get('status')
        if status == 'completed':
            label += ' · 行动提议' if proposal else ' · 待核查草稿' if draft else ' · 回复已返回'
        elif status in {'failure', 'failed', 'interrupted'}:
            label += ' · 调用未完成'
        elif status == 'repair':
            label += ' · 正在修复输出格式'
        else:
            label += ' · 正在生成'
        is_current = message.get('phase') == project.get('phase')
        active = status not in {'completed', 'failure', 'failed', 'interrupted'}
        with st.expander(label, expanded=active and is_current):
            obs = next((o for o in (project.get('background_agent') or {}).get('observations', [])
                        if o.get('call_id') and o['call_id'] == message.get('call_id')), None)
            if proposal:
                st.caption('以下由 Qwen 在行动前生成，不代表工具已成功；实际结果以本条关联记录为准。')
            if draft:
                st.caption('这是模型生成时的草稿，不代表已通过科学审核；正式交付以“资料与成果”及研究状态为准。')
            if obs:
                result = obs.get('result') or {}
                if obs.get('status') == 'failed':
                    st.error('实际行动失败，未完成：' + result.get('error', '请检查工具记录'))
                elif result.get('status') == 'proposal_requires_revision':
                    st.warning('阶段提案未通过审核，需要修正；尚未完成。')
                elif obs.get('status') == 'completed':
                    st.caption('实际行动已返回：' + obs.get('purpose', '') + '（不等于整个研究完成）')
            text = message.get('public_response') or '本次调用没有公开回答正文。'
            render_model_answer(backend_url, project, text, message.get('figures', []), get_json,
                                f"answer_{project['project_id']}_{message.get('call_id', index)}")
            with st.expander('调用凭据'):
                st.caption(f"调用 ID：{message.get('call_id', '未记录')}")
                for receipt in message.get('provider_responses') or []:
                    st.caption(f"模型：{receipt.get('response_model') or receipt.get('requested_model')} · 响应 ID：{receipt.get('response_id') or '未返回'}")
                    timings = [('首个响应', 'first_chunk_seconds'), ('首段正文', 'first_content_seconds'), ('总耗时', 'total_seconds')]
                    st.caption(' · '.join(f'{label} {receipt[key]:.2f} 秒' for label, key in timings
                                           if isinstance(receipt.get(key), (int, float))))
    dialogue = project.get('human_dialogue') or {}
    for answer in dialogue.get('responses', [])[-3:]:
        with st.expander('你的补充 · 已保存到研究上下文', expanded=False):
            st.caption('当时的问题：' + answer.get('prompt', '未记录'))
            st.write(answer.get('response', ''))
            st.caption(answer.get('answered_at', ''))


def render_research_reply(project, submit_reply, busy=False):
    """The only reply form belongs to the current-action area, not the transcript."""
    pending = (project.get('human_dialogue') or {}).get('pending')
    if pending:
        st.markdown(pending['prompt'], unsafe_allow_html=False)
        with st.form(f"research_reply_{pending['request_id']}"):
            response = st.text_area('你的回答', help='这会作为研究输入传给 Qwen，不等于批准联网或执行代码。')
            if st.form_submit_button('提交回答并继续研究', type='primary', disabled=busy):
                if response.strip():
                    submit_reply({'request_id': pending['request_id'], 'response': response})
                else:
                    st.warning('请先填写回答；尚未提交，也没有继续运行。')
