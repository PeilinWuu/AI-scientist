"""Human controls and traceable observations for the adaptive research notebook."""
import base64
import json
from urllib.parse import quote

import streamlit as st


TOOL_LABELS = {
    'update_plan': '制定或调整研究计划', 'search_web': '查找公开资料',
    'search_papers': '检索学术论文', 'read_source': '阅读来源原文',
    'assess_source': '评估来源是否可用', 'inspect_asset': '检查上传资料',
    'prepare_computation': '设计计算方案', 'ask_user': '请求补充信息', 'finish': '提交报告复审',
}


def progress_summary(state):
    """Describe persisted facts, never infer scientific completion from step count."""
    runs = state.get('runs') or []
    successful = sum(r.get('audit', {}).get('status') == 'success' for r in runs)
    sources = state.get('sources') or []
    kept = sum((s.get('assessment') or {}).get('decision') == 'keep' for s in sources)
    status = state.get('status', 'ready')
    if status == 'awaiting_approval':
        headline = '计算方案已准备好，等待你审核'
        next_step = '核对下方的方法、输入和验证标准，展开代码检查；确认后批准执行，或填写修改意见后拒绝此版本。'
    elif status == 'completed':
        headline = '研究报告已形成，并通过自动独立复审'
        next_step = '阅读报告及其局限，核对引用和实际结果。自动复审通过不等于科学结论已被证明。'
    elif status == 'needs_input':
        headline = '研究已暂停，需要你的补充'
        next_step = state.get('message') or '查看下方复审意见，补充数据或说明后继续。'
    elif status == 'execution_interrupted':
        headline = '计算未正常结束，需要人工检查'
        next_step = '检查现有记录，拒绝当前版本后重新规划；系统不会自动重复执行已消费的审批。'
    elif status == 'budget_exhausted':
        headline = '本项目的研究预算已用尽'
        next_step = '检查已取得的证据和未解决问题。预算耗尽不表示研究已完成。'
    elif status == 'error':
        headline = '本次研究遇到问题，已有记录已保留'
        next_step = '查看最近行动及错误说明，补充指示后重试。'
    elif status == 'running':
        headline = '研究正在进行'
        next_step = '下方会更新已完成的行动；你可以要求在当前行动结束后暂停。'
    elif status == 'paused':
        headline = '本批行动已结束，可以继续研究'
        next_step = '点击“开始 / 继续自主研究”，让助手根据现有资料与结果决定下一步。'
    else:
        headline = '研究尚未开始'
        next_step = '检查上方研究问题，按需允许联网，然后开始研究。'
    if successful:
        execution = f'已有 {successful} 次成功计算；这不代表假设已获证实。'
    elif runs:
        execution = f'已尝试 {len(runs)} 次计算，但还没有成功结果。'
    else:
        execution = '尚未执行计算，没有新的计算结果。'
    return {'headline': headline, 'next_step': next_step, 'execution': execution,
            'sources': len(sources), 'kept': kept, 'successful_runs': successful,
            'completed_actions': sum(s.get('status') == 'ok' for s in state.get('steps', []))}


def _items(title, values):
    if values:
        st.markdown(f'**{title}**')
        for value in values:
            st.write('• ' + str(value))


def _error_summary(error):
    text = str(error)
    if 'timeout' in text.lower() or 'timed out' in text.lower():
        return '请求超时，未取得有效结果。可以稍后重试。'
    if '404' in text:
        return '来源链接不存在，未读取到原文。需要更换来源。'
    if 'blocked' in text.lower() and ('DNS' in text or 'Private' in text):
        return '该地址未通过公网访问安全检查，系统没有读取其内容。'
    if any(x in text for x in ('ValidationError', 'StructuredOutputError', 'validation error')):
        return '模型返回的方案格式不符合要求，本次方案未被接受，也没有因此执行代码。'
    return '这次行动未完成，已有资料仍保留。技术原因可在页面底部的调试信息中查看。'


def action_summary(step):
    action = step.get('action') or {}
    observation = step.get('observation') or {}
    tool = action.get('tool')
    if step.get('status') == 'error':
        return _error_summary(observation.get('error'))
    if step.get('status') == 'running':
        return '正在处理，尚未返回结果。'
    if tool == 'prepare_computation':
        return ('已生成待审批方案，尚未执行。' if observation.get('plan_id')
                else observation.get('rationale', '已返回方案评估；是否能执行请看当前研究状态。'))
    if tool == 'read_source':
        return '已读取原文：' + observation.get('title', '公开来源') + '。读取成功不代表结论已被验证。'
    if tool == 'assess_source':
        assessment = observation.get('assessment') or {}
        return ('建议保留该来源。' if assessment.get('decision') == 'keep' else '建议不采用该来源。') + assessment.get('relevance', '')
    if tool == 'search_web':
        return '已获得检索回复；其中的线索仍需读取原文和评估，不能直接当作证据。'
    if tool == 'search_papers':
        return f"找到 {len(observation.get('papers', []))} 条论文元数据，尚不代表读过全文。"
    if tool == 'inspect_asset':
        return '已检查上传资料：' + observation.get('filename', '')
    if tool == 'ask_user':
        return observation.get('request', '需要你补充信息。')
    if tool == 'finish':
        review = observation.get('review') or {}
        return {'accept': '自动独立复审通过。', 'revise': '复审要求修改，还没有完成。',
                'needs_input': '复审要求补充信息。'}.get(review.get('decision'), '报告已提交复审。') + review.get('summary', '')
    if tool == 'update_plan':
        return '已更新研究计划。计划中的步骤仍需实际执行。'
    return '行动已返回；详细结果保存在研究记录中。'


def _report(report):
    st.markdown('### ' + report.get('title', '研究报告'))
    st.write(report.get('question', ''))
    st.markdown('**研究方法**')
    st.write(report.get('methods', ''))
    _items('主要发现', [f.get('statement', '') for f in report.get('findings', [])])
    _items('局限', report.get('limitations'))
    _items('后续工作', report.get('next_steps'))


def render_project_question(project):
    st.subheader('当前研究问题')
    st.write(project.get('objective') or project.get('title') or '此项目尚未记录研究问题。')
    constraints = project.get('constraints') or {}
    if constraints.get('constraints_text'):
        with st.expander('研究约束与补充要求', expanded=False):
            st.write(str(constraints['constraints_text']))


def render_adaptive_workspace(backend_url, project, get_json, post_json, get_text, show_question=True):
    pid = project['project_id']
    state = project.get('research_workspace') or {}
    if show_question:
        render_project_question(project)
    with st.container(border=True):
        st.subheader('自主研究工作台 · Beta')
        st.caption('根据证据选择工具：检索 → 阅读 → 筛选 → 分析 → 独立复审。独立记录，不改写下面的固定阶段流程或已有结果。')
        busy = False
        if st.session_state.get('research_job_id'):
            job = get_json(backend_url, f"/api/research/jobs/{st.session_state.research_job_id}")
            busy = job.get('status') in {'queued', 'running'}
            if job.get('status') == 'failed':
                st.error((job.get('error') or {}).get('error_message', '任务失败'))
        summary = progress_summary(state)
        st.markdown('### ' + summary['headline'])
        st.write(summary['execution'])
        metrics = st.columns(4)
        metrics[0].metric('已完成行动', summary['completed_actions'])
        metrics[1].metric('已读取来源', summary['sources'])
        metrics[2].metric('建议采用来源', summary['kept'])
        metrics[3].metric('成功计算', summary['successful_runs'])
        st.info('下一步：' + summary['next_step'])
        if state.get('status') == 'error' and state.get('message'):
            st.warning(_error_summary(state['message']))
        if project.get('planning_only', True):
            st.info('此项目为仅规划模式：可以检索、阅读与形成文献报告，不能执行计算。需计算时请创建关闭“仅规划”的项目。')
        if state.get('agenda'):
            with st.expander('当前研究计划与待解决问题', expanded=True):
                agenda = state['agenda']
                st.write('研究思路：' + agenda.get('approach', ''))
                _items('待检验的假设', agenda.get('hypotheses'))
                _items('如何判断结果有效', agenda.get('success_criteria'))
                _items('尚缺少的证据', agenda.get('evidence_gaps'))
                _items('计划接下来做什么', agenda.get('next_actions'))
                _items('什么时候停下', agenda.get('stopping_conditions'))
        if state.get('steps'):
            st.markdown('**最近进展**')
            for step in state['steps'][-3:]:
                label = TOOL_LABELS.get(step.get('action', {}).get('tool'), '研究行动')
                description = action_summary(step)
                excerpt = description if len(description) <= 280 else description[:280] + '…（完整说明见研究过程）'
                st.write(f"第 {step['index']} 步 · {label}：{excerpt}")
        network = st.checkbox('允许本批次自主联网检索、阅读和筛选公开资料（会调用 Qwen）',
                              value=False, key=f'workspace_network_{pid}')
        st.caption('允许联网后，来源由模型提出取舍并记录理由；原文匹配不代表科学结论正确。上传资料会进入模型上下文。')
        with st.expander('运行设置'):
            steps = st.number_input('本批次最多行动数', min_value=1, max_value=12, value=6, key=f'workspace_steps_{pid}')
        feedback = st.text_area('给研究助手的补充信息或纠正', key=f'workspace_feedback_{pid}')

        def queue(operation, payload):
            job = post_json(backend_url, f'/api/research/{pid}/workspace/{operation}', payload)
            st.session_state.research_job_id = job['job_id']
            st.rerun()

        if st.button('开始 / 继续自主研究', key=f'workspace_run_{pid}', disabled=busy or
                     state.get('status') in {'awaiting_approval', 'execution_interrupted', 'budget_exhausted'}):
            queue('run', {'max_steps': int(steps), 'allow_network': network, 'feedback': feedback})
        if busy and st.button('当前行动结束后暂停', key=f'workspace_pause_{pid}'):
            post_json(backend_url, f'/api/research/{pid}/workspace/pause', {})
            st.info('已请求暂停；不会中断正在进行的模型请求或写入。')
        if st.button('刷新研究进展', key=f'workspace_refresh_{pid}'):
            st.session_state.research_project = get_json(backend_url, f'/api/research/{pid}')
            st.rerun()
        pending = state.get('pending_computation') or {}
        if pending:
            plan = pending['plan']
            st.markdown('**待审批的计算方案**')
            st.write('计算目的：' + plan.get('rationale', ''))
            st.write('准备怎样做：' + plan.get('method', ''))
            if plan.get('input_files'):
                _items('将读取的数据文件', plan['input_files'])
            else:
                st.write('输入：方案不读取上传数据。请确认任务确实允许不依赖实测数据的计算或模拟。')
            _items('预期生成的文件（尚未生成）', plan.get('expected_outputs'))
            _items('如何验证计算结果', plan.get('validation_checks'))
            st.write('停止条件：' + plan.get('stopping_rule', ''))
            with st.expander('核对假设与具体指标'):
                _items('计算假设', plan.get('assumptions'))
                _items('必须输出的指标', plan.get('required_metrics'))
            with st.expander('审核生成的 Python 代码（执行前请查看）'):
                st.code(plan['code'], language='python')
                st.download_button('下载待审批代码', plan['code'], file_name='proposed_research.py')
            approved = st.checkbox('我已核对输入、方法、指标和代码，批准执行这一版本。',
                                   key=f"workspace_approve_{pending['plan_id']}")
            if st.button('批准此版本并执行', key=f'workspace_execute_{pid}', disabled=busy or not approved or state.get('status') != 'awaiting_approval'):
                queue('execute', {'plan_id': pending['plan_id']})
            if st.button('拒绝此版本，要求重新规划', key=f'workspace_reject_{pid}', disabled=busy):
                post_json(backend_url, f'/api/research/{pid}/workspace/reject', {'plan_id': pending['plan_id'], 'reason': feedback})
                st.session_state.research_project = get_json(backend_url, f'/api/research/{pid}')
                st.rerun()
        if state.get('steps'):
            with st.expander('完整研究过程'):
                for step in state['steps']:
                    action = step['action']
                    observation = step.get('observation') or {}
                    status = {'ok': '已返回结果', 'error': '未完成', 'running': '进行中'}.get(step['status'], '等待处理')
                    st.markdown(f"**第 {step['index']} 步 · {TOOL_LABELS.get(action['tool'], '研究行动')} · {status}**")
                    st.write('目的：' + action['purpose'])
                    st.write(action_summary(step))
                    for key, label in [('query', '检索词'), ('url', '访问链接')]:
                        if action.get('arguments', {}).get(key):
                            st.write(label + '：' + action['arguments'][key])
                    if action['tool'] == 'search_web' and observation.get('reply'):
                        st.write('检索回复（仍需核实）：' + observation['reply'])
                    if action['tool'] == 'search_papers':
                        for paper in observation.get('papers', []):
                            st.write(paper.get('title', '未命名论文'))
                            st.caption('DOI：' + str(paper.get('doi') or '未提供'))
        if state.get('sources'):
            with st.expander('来源筛选台账（包括被拒绝的来源）'):
                for source in state['sources']:
                    st.write(source['source_id'], source['title'], source['url'])
                    assessment = source.get('assessment') or {}
                    st.write('筛选结果：' + {'keep': '建议采用', 'reject': '不采用'}.get(assessment.get('decision'), '已读取，尚未评估'))
                    for key, label in [('relevance', '与问题的关系'), ('credibility', '可信度判断'), ('quote', '核对的原文'), ('supported_statement', '支持的有限结论')]:
                        if assessment.get(key):
                            st.write(label + '：' + assessment[key])
                    _items('来源局限', assessment.get('limitations'))
                    st.caption('原文匹配只证明该文字存在，不等于结论正确。')
        if state.get('runs'):
            with st.expander('实际执行记录与产物'):
                for run in state['runs']:
                    st.write('执行 ID：', run['audit']['run_id'])
                    audit = run['audit']
                    st.write('执行结果：' + ('计算成功，仍需科学复审' if audit['status'] == 'success' else '计算失败，不能当作科研结论'))
                    if audit.get('error'):
                        st.warning(_error_summary(audit['error']))
                    result = audit.get('result') or {}
                    for name, value in (result.get('metrics') or {}).items():
                        st.write(f'{name}：{value}')
                    _items('结果局限', result.get('limitations'))
                files = sorted({f['relative_path'] for r in state['runs'] for f in r['audit'].get('artifacts', [])})
                if files:
                    path = st.selectbox('自主计算产物', files, key=f'workspace_file_{pid}')
                    if st.button('读取自主计算产物', key=f'workspace_load_{pid}'):
                        item = get_json(backend_url, f'/api/research/{pid}/computation/file?path={quote(path, safe="")}')
                        content = base64.b64decode(item['content_base64'])
                        if item['filename'].endswith('.png'):
                            st.image(content)
                        st.download_button('下载自主计算产物', content, file_name=item['filename'])
        if state.get('reviews'):
            with st.expander('独立复审意见', expanded=state.get('status') == 'needs_input'):
                for index, review in enumerate(state['reviews'], 1):
                    verdict = {'accept': '接受报告', 'revise': '要求修订', 'needs_input': '需要补充信息'}.get(review.get('decision'), '待确认')
                    st.markdown(f'**第 {index} 次复审：{verdict}**')
                    st.write(review.get('summary', ''))
                    _items('需要注意的问题', review.get('issues'))
        if state.get('report'):
            report = get_text(backend_url, f'/api/research/{pid}/workspace/report.md')
            st.markdown(report)
            st.download_button('下载自主研究报告', report, file_name='autonomous_research.md')
        if state.get('report_history'):
            with st.expander('历史报告版本（不代表当前问题已完成）'):
                for report in state['report_history']:
                    _report(report)
        if state:
            st.download_button('下载完整自主研究台账', json.dumps(state, ensure_ascii=False, indent=2),
                               file_name='research_workspace.json', mime='application/json')
            with st.expander('开发者调试与原始数据'):
                if st.checkbox('显示原始 JSON（用于排查问题）', key=f'workspace_debug_{pid}'):
                    st.json(state)
