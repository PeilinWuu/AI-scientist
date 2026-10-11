"""Public Qwen messages, distinct from tool facts and private model reasoning."""
import streamlit as st


def render_research_conversation(project, messages, phase_labels):
    st.subheader('与 Qwen 一起研究')
    st.caption('以下是 Qwen 本次调用直接生成的公开回答，未经模板改写；不包含模型私有思维链。流式草稿尚未通过结构校验，工具事实与审批以记录为准。')
    if not messages:
        st.info('Qwen 的回答会显示在这里。请在顶部开始或继续研究；历史研究产物可在“资料与成果”中查看。')
    show_history = st.checkbox('展开全部阶段记录', value=False, key=f"all_messages_{project['project_id']}")
    visible = messages if show_history else messages[-1:]
    if messages and not show_history:
        st.caption(f'显示最新进展 · 已保留 {len(messages)} 条模型记录')
    for index, message in enumerate(visible):
        phase = phase_labels.get(message.get('phase'), message.get('phase', '研究'))
        label = f"{phase} · {message.get('model') or 'Qwen'}"
        label += ' · 已完成' if message.get('status') == 'completed' else ' · 草稿 / 未完成'
        with st.expander(label, expanded=index == len(visible) - 1):
            text = message.get('public_response') or '本次调用没有公开回答正文。'
            # Show a verbatim excerpt, never a fabricated scientific summary.
            if len(text) > 900:
                st.markdown(text[:900] + '\n\n…（原文节选，完整回答见下方）', unsafe_allow_html=False)
                with st.expander('查看完整模型回答', expanded=False):
                    st.markdown(text, unsafe_allow_html=False)
            else:
                st.markdown(text, unsafe_allow_html=False)
            with st.expander('调用凭据'):
                st.caption(f"调用 ID：{message.get('call_id', '未记录')}")
                for receipt in message.get('provider_responses') or []:
                    st.caption(f"模型：{receipt.get('response_model') or receipt.get('requested_model')} · 响应 ID：{receipt.get('response_id') or '未返回'}")
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
