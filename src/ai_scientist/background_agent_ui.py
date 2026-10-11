"""Readable controls for model-led background research in the fixed workflow."""
import streamlit as st


ACTION_LABELS = {
    'search_web': '联网搜索', 'search_papers': '查找论文', 'read_source': '读取原文',
    'inspect_asset': '阅读上传资料', 'assess_source': '判断资料', 'finish': '提出阶段结论',
    'ask_user': '请求你的帮助', 'request_scope_revision': '请求调整研究依据',
    'download_data': '下载并登记公开数据',
}


def render_background_agent(project, busy, action, show_debug=False):
    pid, phase = project['project_id'], project['phase']
    state = project.get('background_agent') or {}
    scope = state.get('scope') or {}
    prefix = f'/api/research/{pid}/background-agent'
    st.subheader('背景研究：由 Qwen 判断、调用工具，由你确认依据')
    st.caption('固定科研流程保持不变。本阶段不会执行实验，也不会自动批准研究结论。')
    if not scope:
        st.info('继续当前阶段后，Qwen 将先判断这个问题是否需要外部资料，并提出依据和验证计划。')
        return
    labels = {'required': '需要外部资料', 'optional': '外部资料可选', 'not_needed': '建议不依赖外部文献'}
    st.markdown(f"**Qwen 的判断：{labels.get(scope.get('external_information'), '待判断')}**")
    st.write(scope.get('rationale', ''))
    for title, key in [('待解决的信息缺口', 'information_gaps'), ('后续需要完成的验证（尚未执行）', 'validation_basis'),
                       ('初始检索方向（执行中可调整）', 'initial_queries')]:
        if scope.get(key):
            st.markdown(f'**{title}**')
            for value in scope[key]:
                st.write('• ' + value)
    st.caption('建议停止条件：' + scope.get('stopping_rule', '尚未说明'))
    observations = state.get('observations') or []
    if observations:
        st.markdown('**实际进展**')
        for index, obs in enumerate(observations):
            result = obs.get('result') or {}
            status = {'completed': '已完成', 'failed': '失败', 'interrupted': '执行中或被中断，尚无成功记录'}.get(obs.get('status'), '未知')
            with st.expander(f"{index + 1}. {ACTION_LABELS.get(obs.get('action'), obs.get('action'))} · {status}",
                             expanded=index == len(observations) - 1):
                st.write(obs.get('purpose', ''))
                if result.get('error'):
                    st.error(result['error'])
                if result.get('status') == 'tool_not_executed':
                    st.warning('模型请求返回了，但没有实际调用搜索工具。这次不计作成功搜索，也没有形成证据。')
                if result.get('status') == 'proposal_requires_revision':
                    st.warning('独立模型核查发现阶段总结与实际记录不符，已返回模型修正，尚未提交采用。')
                if 'candidates' in result:
                    st.write(f"取得 {len(result['candidates'])} 条新的候选资料（不等于正式证据）。")
                for key in ['summary', 'request', 'message', 'relevance', 'credibility']:
                    if result.get(key):
                        st.write(result[key])
                if result.get('text'):
                    st.text(result['text'][:3000])
                    st.caption('此处展示节选；完整可用文本与读取范围保存在本次观察记录中。')
    if phase == 'SEARCH_PLAN_REVIEW':
        if state.get('status') == 'budget_paused':
            st.warning('本次授权预算已用完。已有记录保留；继续需要你重新授权。')
        acknowledge = st.checkbox('我已审阅以上资料需求、验证计划与停止条件', key=f'basis_ack_{pid}_{state.get("scope_id")}')
        network = st.checkbox('允许本阶段访问公共网络（检索和读取来源）', value=False, key=f'basis_network_{pid}')
        actions = st.number_input('本次允许的工具/判断动作数', min_value=1, max_value=40, value=12, key=f'basis_actions_{pid}')
        budget = project.get('budget') or {}
        st.caption(f"项目已尝试 {budget.get('attempted_model_calls', 0)} 次模型请求；搜索模型请求也计入预算。")
        calls = st.number_input('项目模型调用总上限（增加会允许更多 API 消耗）', min_value=max(1, budget.get('attempted_model_calls', 0)),
                                max_value=300, value=min(300, max(1, budget.get('max_model_calls', 30), budget.get('attempted_model_calls', 0))),
                                key=f'basis_budget_{pid}')
        if st.button('批准工具范围并继续研究', disabled=busy or not acknowledge, key=f'basis_authorize_{pid}', type='primary'):
            action(prefix + '/authorize', {'scope_id': state['scope_id'], 'allow_network': network,
                                          'action_budget': int(actions), 'max_model_calls': int(calls)})
    if phase == 'BACKGROUND_RESEARCH':
        st.info('工具范围已获批。请通过顶部“当前进展与下一步”继续或暂停；达到审批或预算关卡时会停下。')
    selected = []
    for source in state.get('sources') or []:
        sid = source['candidate_id']
        assessment = (state.get('assessments') or {}).get(sid) or {}
        eligible = assessment.get('decision') == 'keep' and assessment.get('quote_matched')
        with st.expander(source.get('title') or source.get('url') or sid, expanded=bool(eligible)):
            if source.get('url'):
                st.markdown(f"[打开来源]({source['url']})")
            if assessment:
                basis = '已读取的正文' if assessment.get('reading_basis') == 'source_text' else '仅书目信息（未阅读全文）'
                decision = {'keep': '建议采用', 'reject': '建议排除', 'uncertain': '暂不能判断'}.get(assessment.get('decision'), '待判断')
                st.caption(f'Qwen 基于{basis}：{decision}')
                for key in ['summary', 'relevance', 'credibility']:
                    if assessment.get(key):
                        st.write(assessment[key])
                if assessment.get('quote'):
                    st.text('原文摘录：' + assessment['quote'])
                for limit in assessment.get('limitations') or []:
                    st.write('局限：' + limit)
            else:
                st.caption('已发现，尚未由模型评估；缺少摘要不代表不相关。')
                if source.get('snippet'):
                    st.write(source['snippet'])
            if eligible and phase == 'HUMAN_SOURCE_REVIEW' and state.get('status') == 'awaiting_adoption':
                if st.checkbox('采用这条已阅读资料', value=False, key=f'basis_source_{pid}_{sid}'):
                    selected.append(sid)
    if phase == 'HUMAN_SOURCE_REVIEW':
        st.caption('以下仅为背景依据提案。本阶段没有计算执行工具，不代表实验或数值验证已经完成。')
        st.write(state.get('proposal') or state.get('message') or '需要你审查或补充信息。')
        if state.get('proposal_review'):
            with st.expander('独立模型对依据提案的核查'):
                st.write(state['proposal_review'].get('rationale', ''))
        if state.get('status') == 'awaiting_adoption':
            independent = False
            if scope.get('external_information') in {'optional', 'not_needed'}:
                independent = st.checkbox('确认本阶段不采用外部资料；后续仍必须完成上述推导、数据或实验验证', key=f'basis_independent_{pid}')
            if st.button('确认研究依据并继续研究', disabled=busy or (not selected and not independent), key=f'basis_adopt_{pid}', type='primary'):
                action(prefix + '/adopt', {'source_ids': selected, 'confirm_basis': independent,
                                           'scope_id': state['scope_id'], 'proposal_id': state['proposal_id']})
    if phase in {'BACKGROUND_RESEARCH', 'SEARCH_PLAN_REVIEW', 'HUMAN_SOURCE_REVIEW'}:
        if state.get('feedback'):
            with st.expander('已收到的补查与修订意见'):
                for message in state['feedback'][-5:]:
                    st.write(message)
                st.caption('已保存并将送入后续背景调用；是否落实需查看后续回答与工具记录。')
        with st.expander('要求补查、纠正或重新判断资料需求'):
            feedback = st.text_area('你的意见（将原样交给 Qwen）', key=f'basis_feedback_{pid}')
            regenerate = st.checkbox('重新提出研究依据（保留旧记录，撤销本轮工具授权）', value=state.get('status') == 'scope_revision', key=f'basis_regenerate_{pid}')
            if st.button('提交意见', disabled=busy or not feedback.strip(), key=f'basis_feedback_send_{pid}'):
                action(prefix + '/feedback', {'message': feedback, 'regenerate': regenerate})
    if show_debug:
        with st.expander('背景研究结构化记录（开发者）'):
            st.json(state)
