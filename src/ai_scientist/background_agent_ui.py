"""Readable controls for model-led background research in the fixed workflow."""
import streamlit as st
from urllib.parse import urlsplit
from contextlib import nullcontext
from src.ai_scientist.background_agent import source_adoptable


ACTION_LABELS = {
    'search_web': '联网搜索', 'search_papers': '查找论文', 'read_source': '读取原文',
    'inspect_asset': '阅读上传资料', 'assess_source': '判断资料', 'finish': '提出阶段结论',
    'ask_user': '请求你的帮助', 'request_scope_revision': '请求调整研究依据',
    'download_data': '下载并登记公开数据',
}


def readable_error(error):
    if '403' in error or 'CAPTCHA' in error or 'access challenge' in error:
        return '来源网站拒绝自动读取。未取得论文正文；系统会保留记录并尝试其他公开来源。'
    if 'previously failed' in error:
        return '这个地址此前读取失败，已避免重复请求。可以换来源，或说明为什么需要重试。'
    if 'quote does not exactly match' in error:
        return '引用没有匹配已读取的原文，本次评估未保存。需要重新读取对应段落并使用真实摘录。'
    if 'observation' in error.lower():
        return '引用的工具记录不可用，本次行动未执行。需要重新获取来源或使用可用的历史记录。'
    return '本次行动未完成，已有成功记录保留。请查看具体原因后调整或补充研究要求。'


def render_background_agent(project, busy, action, show_debug=False):
    pid, phase = project['project_id'], project['phase']
    state = project.get('background_agent') or {}
    scope = state.get('scope') or {}
    prefix = f'/api/research/{pid}/background-agent'
    st.subheader('选择本轮研究依据')
    if not scope:
        st.info('继续当前阶段后，Qwen 将先判断这个问题是否需要外部资料，并提出依据和验证计划。')
        return
    labels = {'required': '需要外部资料', 'optional': '外部资料可选', 'not_needed': '建议不依赖外部文献'}
    sources = state.get('sources') or []
    st.write(f"发现 {len(sources)} 条来源 · 已读取 {sum(bool(s.get('read_ranges')) for s in sources)} 篇的内容 · "
             f"已保存 {len(state.get('assessments') or {})} 条判断 · 已采用 {len(project.get('evidence') or [])} 条证据")
    if state.get('message') and state.get('status') == 'needs_input':
        st.warning(state['message'])
    adopting = phase == 'HUMAN_SOURCE_REVIEW' and state.get('status') == 'awaiting_adoption'
    selected = []
    if adopting:
        st.caption('选择愿意采用的来源；采用资料不等于认可其中所有结论。原文和判断可展开查看。')
        for source in sources:
            sid = source['candidate_id']
            assessment = (state.get('assessments') or {}).get(sid) or {}
            if not source_adoptable(assessment):
                continue
            label = (source.get('title') or source.get('url') or '未命名资料').removesuffix(' Lock')
            if st.checkbox(label, key=f'basis_source_{pid}_{sid}', disabled=busy):
                selected.append(sid)
            if assessment.get('summary'):
                preview = assessment['summary']
                st.caption(preview[:180] + ('…（展开阅读全文）' if len(preview) > 180 else ''))
            with st.expander('阅读依据与局限 · ' + (source.get('source_alias') or sid)):
                if source.get('url'):
                    st.markdown(f"[打开原文]({source['url']})")
                st.write(assessment.get('summary', ''))
                st.write(assessment.get('relevance', ''))
                for limit in assessment.get('limitations') or []:
                    st.write('• ' + limit)
                st.caption('只读取过部分内容；解释仍需结合原文判断。')
                if assessment.get('quote'):
                    st.text(assessment['quote'])
        independent = False
        if scope.get('external_information') in {'optional', 'not_needed'}:
            independent = st.checkbox('本轮不采用外部资料，按已确认的其他依据继续', key=f'basis_independent_{pid}')
        if st.button('确认研究依据并继续研究', disabled=busy or (not selected and not independent),
                     key=f'basis_adopt_{pid}', type='primary'):
            action(prefix + '/adopt', {'source_ids': selected, 'confirm_basis': independent,
                'scope_id': state['scope_id'], 'proposal_id': state['proposal_id']})
    if state.get('partial_answer'):
        with st.expander('阅读阶段回答（非最终报告）', expanded=False) if adopting else nullcontext():
            st.markdown(state['partial_answer']['markdown'])
            st.caption('基于实际阅读的阶段回答，尚非最终报告。')
            for url in state['partial_answer'].get('sources', []):
                label = next((s.get('title') for s in sources if s.get('url') == url), None) or url
                st.markdown(f'[{label}]({url})')
            st.download_button('保存阶段回答', data='> 阶段阅读回答，非最终报告；不代表结论已核验。\n\n' + state['partial_answer']['markdown'],
                               file_name=f'{pid}_reading_answer.md', mime='text/markdown', key=f'basis_download_{pid}')
        if state.get('note_validation_errors'):
            st.warning('部分证据说明尚未通过核对；阶段回答不能视为已核验结论。')
    if (not busy and phase in {'BACKGROUND_RESEARCH', 'HUMAN_SOURCE_REVIEW', 'SEARCH_PLAN_REVIEW'}
            and any(s.get('shown_excerpts') for s in sources)):
        label = '重新整理阶段回答' if state.get('status') == 'awaiting_adoption' else '停止继续检索，整理已有资料'
        if st.button(label, key=f'basis_consolidate_{pid}'):
            action(prefix + '/feedback', {'message': '请基于实际已读资料整理有限回答，明确缺口，不再继续检索。',
                                         'regenerate': False, 'consolidate': True})
    with st.expander('研究范围、依据与停止条件', expanded=phase == 'SEARCH_PLAN_REVIEW' and state.get('status') != 'budget_paused'):
        st.markdown(f"**Qwen 的判断：{labels.get(scope.get('external_information'), '待判断')}**")
        st.write(scope.get('rationale', ''))
        for title, key in [('待解决的信息缺口', 'information_gaps'), ('后续验证（尚未执行）', 'validation_basis'),
                           ('检索方向', 'initial_queries')]:
            if scope.get(key):
                st.write(title)
                for value in scope[key]:
                    st.write('• ' + value)
        st.caption('停止条件：' + scope.get('stopping_rule', '尚未说明'))
    observations = state.get('observations') or []
    if observations and st.checkbox(f'查看工具过程（{len(observations)} 条）', key=f'basis_tools_{pid}'):
        st.markdown('**实际进展**')
        show_all = st.checkbox('展开全部工具记录', key=f'basis_history_{pid}')
        first = 0 if show_all else max(0, len(observations) - 3)
        for index, obs in enumerate(observations[first:], start=first):
            result = obs.get('result') or {}
            status = {'completed': '行动已返回', 'failed': '失败，未完成', 'interrupted': '执行中或被中断，尚无成功记录'}.get(obs.get('status'), '未知')
            if result.get('status') == 'proposal_requires_revision':
                status = '审核退回，未完成'
            with st.expander(f"{index + 1}. {ACTION_LABELS.get(obs.get('action'), obs.get('action'))} · {status}",
                             expanded=False):
                st.write(obs.get('purpose', ''))
                if obs.get('started_at'):
                    st.caption(f"本次行动耗时 {obs.get('duration_seconds', '进行中')} 秒（可能包含工具和模型处理）")
                if result.get('error'):
                    st.error(readable_error(result['error']))
                    with st.expander('具体错误记录'):
                        st.text(result['error'])
                if result.get('status') == 'tool_not_executed':
                    st.warning('模型请求返回了，但没有实际调用搜索工具。这次不计作成功搜索，也没有形成证据。')
                if result.get('status') == 'proposal_requires_revision':
                    st.warning('独立模型核查发现阶段总结与实际记录不符，已返回模型修正，尚未提交采用。')
                if 'candidates' in result:
                    st.write(f"取得 {len(result['candidates'])} 条新的候选资料（不等于正式证据）。")
                for key in ['summary', 'request', 'message', 'relevance', 'credibility']:
                    if result.get(key):
                        st.write('资料已整理，等待你确认采用。' if result[key] == 'Waiting for human adoption; no scientific approval granted' else result[key])
                if result.get('text'):
                    st.text(result['text'][:3000])
                    st.caption('此处展示节选；完整可用文本与读取范围保存在本次观察记录中。')
    if phase == 'SEARCH_PLAN_REVIEW':
        renewal = state.get('status') == 'budget_paused' and (state.get('authorization') or {}).get('scope_id') == state.get('scope_id')
        if renewal:
            st.info('额度已用完，检索范围和网络许可没有改变。这里只追加额度，不重新授权。')
            acknowledge = True
            network = state['authorization']['allow_network']
        else:
            acknowledge = st.checkbox('我已审阅以上资料需求、验证计划与停止条件', key=f'basis_ack_{pid}_{state.get("scope_id")}')
            network = st.checkbox('允许本阶段访问公共网络（检索和读取来源）', value=False, key=f'basis_network_{pid}_{state.get("scope_id")}')
        actions = st.number_input('本次允许的工具/判断动作数', min_value=1, max_value=40, value=12, key=f'basis_actions_{pid}')
        budget = project.get('budget') or {}
        st.caption(f"项目已尝试 {budget.get('attempted_model_calls', 0)} 次模型请求；搜索模型请求也计入预算。")
        calls = st.number_input('项目模型调用总上限（增加会允许更多 API 消耗）', min_value=max(1, budget.get('attempted_model_calls', 0)),
                                max_value=300, value=min(300, max(1, budget.get('max_model_calls', 30), budget.get('attempted_model_calls', 0))),
                                key=f'basis_budget_{pid}')
        if st.button('追加额度并继续研究' if renewal else '批准工具范围并继续研究', disabled=busy or not acknowledge, key=f'basis_authorize_{pid}', type='primary'):
            action(prefix + '/authorize', {'scope_id': state['scope_id'], 'allow_network': network,
                                          'action_budget': int(actions), 'max_model_calls': int(calls)})
    if phase == 'BACKGROUND_RESEARCH':
        st.info('工具范围已获批。请通过顶部“当前进展与下一步”继续或暂停；达到审批或预算关卡时会停下。')
    unread = [s for s in sources if not s.get('read_ranges') and not (state.get('assessments') or {}).get(s['candidate_id'])]
    show_unread = st.checkbox(f'显示尚未阅读的来源（{len(unread)} 条）', value=not any(s.get('read_ranges') for s in sources),
                             # Entering adoption is a new review context: do not
                             # carry the initial discovery view's "show all"
                             # default across and bury assessed sources.
                             key=f'basis_unread_{pid}_{phase == "HUMAN_SOURCE_REVIEW"}') if unread and not adopting else False
    for source in ([] if adopting else sources):
        sid = source['candidate_id']
        assessment = (state.get('assessments') or {}).get(sid) or {}
        if not show_unread and not assessment and not source.get('read_ranges'):
            continue
        eligible = source_adoptable(assessment)
        label = source.get('title')
        if not label:
            host = urlsplit(source.get('url') or '').hostname or '未知网站'
            label = f"标题待核对 · {host} · {source.get('source_alias') or sid}"
        with st.expander(label.removesuffix(' Lock'), expanded=False):
            if source.get('url'):
                st.markdown(f"[打开来源]({source['url']})")
            if source.get('read_ranges'):
                st.caption('已读取部分内容；这不表示整篇论文已经读完。')
            elif source.get('retrieval_status') == 'read_failed':
                st.caption('正文读取失败；目前只有搜索线索。')
            if not source.get('title'):
                st.caption('检索结果未提供标题，尚需读取原文核对；这本身不能判定资料是否相关。')
            if assessment:
                basis = '已读取的正文' if assessment.get('reading_basis') == 'source_text' else '仅书目信息（未阅读全文）'
                decision = {'keep': '建议采用', 'reject': '建议排除', 'uncertain': '暂不能判断'}.get(assessment.get('decision'), '待判断')
                st.caption(f'Qwen 基于{basis}：{decision}')
                if assessment.get('assessment_mode') == 'reading_note':
                    st.info('已核对真实阅读和摘录；以下是 Qwen 的阅读笔记，解释尚未独立核查。采用来源不表示认可其全部结论。')
                elif assessment.get('decision') == 'keep' and not (assessment.get('semantic_review') or {}).get('supported'):
                    st.warning('这是旧版摘录校验建议，尚未经过结论忠实度核查；继续背景研究后核查，不能直接采用。')
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
        st.caption('本阶段是资料阅读与整理，没有执行新实验或计算。')
        st.write(state.get('proposal') or state.get('message') or '需要你审查或补充信息。')
        if state.get('proposal_review'):
            with st.expander('独立模型对依据提案的核查'):
                st.write(state['proposal_review'].get('rationale', ''))
        if state.get('status') == 'awaiting_adoption':
            legacy = [a for a in (state.get('assessments') or {}).values()
                      if a.get('decision') == 'keep' and a.get('assessment_mode') != 'reading_note'
                      and not (a.get('semantic_review') or {}).get('supported')]
            if legacy:
                st.warning(f'有 {len(legacy)} 条旧版建议还需原文忠实度核查；核查前不能采用。')
                if st.button('重新核查旧版资料建议', disabled=busy, key=f'basis_reaudit_{pid}'):
                    action(prefix + '/feedback', {'message': '请依据实际已读原文重新核查旧版资料建议，再提出可供采用的依据；不要跳过摘录和科学解释核查。', 'regenerate': False})
    if phase in {'BACKGROUND_RESEARCH', 'SEARCH_PLAN_REVIEW', 'HUMAN_SOURCE_REVIEW'}:
        if state.get('feedback'):
            with st.expander('已收到的补查与修订意见'):
                for message in state['feedback'][-5:]:
                    st.write(message)
                st.caption('已保存并将送入后续背景调用；是否落实需查看后续回答与工具记录。')
        if (project.get('human_dialogue') or {}).get('pending'):
            st.info('当前有待回答的问题，请使用顶部的“提交回答并继续研究”，无需另行提交意见。')
            return
        with st.expander('要求补查、纠正或重新判断资料需求'):
            with st.form(f'basis_feedback_form_{pid}'):
                feedback = st.text_area('你的意见（将原样交给 Qwen）', key=f'basis_feedback_{pid}')
                regenerate = st.checkbox('重新提出研究依据（保留旧记录，撤销本轮工具授权）', value=state.get('status') == 'scope_revision', key=f'basis_regenerate_{pid}')
                if st.form_submit_button('提交意见并继续研究', disabled=busy):
                    if feedback.strip():
                        action(prefix + '/feedback', {'message': feedback, 'regenerate': regenerate})
                    else:
                        st.warning('请填写意见；尚未提交。')
    if show_debug:
        with st.expander('背景研究结构化记录（开发者）'):
            st.json(state)
