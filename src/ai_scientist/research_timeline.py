"""One readable timeline and one composer, backed by persisted research records."""
import hashlib
import uuid
import os

import streamlit as st
from src.ui_time import parse_datetime, format_local_datetime, ui_timezone

from src.ai_scientist.product_ui import render_model_answer
from src.ai_scientist.literature_experience import current_draft
from src.ai_scientist.workflow_ui_state import active_human_question
from src.ai_scientist.tool_presentation import tool_view
from src.ai_scientist.markdown_links import readable_links


def live_answer(messages, job):
    """Only this running job's writer may replace the visible current draft."""
    if not job or job.get('status') not in {'queued', 'running'}:
        return None
    return next((m for m in reversed(messages)
        if m.get('job_id') == job.get('job_id') and m.get('message_kind') == 'research_draft'
        and m.get('agent_name') == 'scientific_synthesizer'
        and m.get('public_response') and m.get('status') not in {'failure', 'failed', 'interrupted'}), None)


def timeline_entries(project, messages, live=None):
    state = project.get('literature_review') or {}
    versions = [state.get('draft') or {'report_markdown': current_draft(state)}, *state.get('draft_history', [])]
    draft_ids = {v.get('call_id') for v in versions if v.get('call_id')}
    draft_texts = {v.get('report_markdown') for v in versions if v.get('report_markdown')}
    users = (project.get('human_dialogue') or {}).get('messages', [])
    entries = [{'kind': 'user', 'time': u.get('created_at', ''), 'data': u} for u in users]
    if project.get('objective'):
        entries.append({'kind': 'question', 'time': project.get('created_at', ''),
                        'data': {'text': project['objective']}})
    for asset in project.get('research_assets') or []:
        entries.append({'kind': 'asset', 'time': asset.get('created_at', ''), 'data': asset})
    for m in messages:
        if ((live and m.get('call_id') == live.get('call_id')) or m.get('call_id') in draft_ids
                or m.get('public_response') in draft_texts):
            continue  # The canonical answer and version history own this text.
        entries.append({'kind': 'model', 'time': m.get('started_at') or m.get('timestamp', ''), 'data': m})
    # Older projects can have saved replies without a job-stream record.
    seen = {m.get('dialogue_message_id') for m in messages}
    for u in users:
        if u.get('response') and u['id'] not in seen:
            entries.append({'kind': 'model', 'time': u.get('answered_at', u.get('created_at', '')),
                            'data': {'public_response': u['response'], 'status': 'completed',
                                     'dialogue_message_id': u['id']}})
    return sorted(entries, key=entry_time)


def entry_time(entry):
    parsed = parse_datetime(entry.get('time') or None)
    return parsed.timestamp() if parsed else float('-inf')


def timeline_partition(entries, limit=3):
    """Fold only a contiguous prefix: never lift selected old replies above newer ones."""
    cut = max(0, len(entries) - limit)
    return list(range(cut)), list(range(cut, len(entries)))


def reading_draft_state(project, messages):
    """A saved reading note is a draft, not a second completed research answer."""
    background = project.get('background_agent') or {}
    reading = background.get('partial_answer') or {}
    text = reading.get('markdown', '').strip()
    review = background.get('proposal_review') or {}
    needs_revision = bool(review and (review.get('decision') != 'ready_for_human'
                          or review.get('unsupported_completion_claims')))
    return {
        'text': text,
        'already_in_timeline': bool(text and any(
            m.get('public_response', '').strip() == text for m in messages)),
        'label': '阅读草稿 · 资料整理仍需修订' if needs_revision else '阅读草稿 · 尚非最终研究结果',
        'needs_revision': needs_revision,
        'review_note': review.get('rationale', '') if needs_revision else '',
    }


def render_timeline(project, messages, phase_labels, backend_url='', get_json=None, live=None, render_answer=None):
    entries = timeline_entries(project, messages, live)
    reading_state = reading_draft_state(project, messages)
    observations = (project.get('background_agent') or {}).get('observations', [])
    state = project.get('literature_review') or {}
    has_answer = bool(current_draft(state) or live)
    if render_answer and has_answer:
        texts = {current_draft(state)}
        times = [m['timestamp'] for m in messages
                 if m.get('timestamp') and m.get('public_response') in texts]
        # Existing documents stay at their original place; later discussion follows them.
        anchor = (live.get('started_at') or live.get('timestamp', '')) if live else (
            max(times, key=lambda t: entry_time({'time': t})) if times else state.get('answer_created_at', ''))
        entries.append({'kind': 'answer', 'time': anchor, 'data': {}})
        entries.sort(key=entry_time)
    if reading_state['text'] and not has_answer and not reading_state['already_in_timeline']:
        entries.append({'kind': 'reading', 'time': (project.get('background_agent') or {}).get('partial_answer', {}).get('created_at', ''), 'data': {}})
        entries.sort(key=entry_time)
    user_numbers = {e['data'].get('id'): i + 1 for i, e in enumerate(entries) if e['kind'] == 'user'}
    if entries:
        st.subheader('研究对话与进展')
        st.caption(f'从上到下：较早 → 最新 · 时间：{ui_timezone().key} · 序号表示记录顺序，不表示完成度')
        st.markdown('[跳到最新进展](#research-latest) · [写消息 / 添加附件](#research-composer)')

    def render(entry, index):
        value = entry['data']
        role = {'user': '你', 'question': '你 · 初始问题', 'asset': '附件已保存',
                'answer': 'Qwen · 当前研究回答', 'reading': 'Qwen · 阅读草稿'}.get(entry['kind'], 'Qwen')
        stamp = format_local_datetime(entry.get('time')) if parse_datetime(entry.get('time') or None) else '历史记录，时间未保存'
        heading = f"#{index + 1} · {role} · `{stamp}`"
        if entry['kind'] == 'answer':
            st.caption(heading)
            render_answer()
            return
        if entry['kind'] == 'reading':
            st.caption(heading)
            with st.expander(reading_state['label'], expanded=False):
                st.markdown(readable_links(reading_state['text']))
            return
        if entry['kind'] == 'asset':
            st.caption(heading)
            st.write('📎 ' + value.get('filename', '附件'))
            st.caption('已解析，可供后续研究读取；不表示已被采用。' if value.get('parsing_status') == 'parsed'
                       else '文件已保存，但解析尚未成功；请在研究档案检查原因。')
            return
        if entry['kind'] == 'question':
            with st.chat_message('user'):
                st.caption(heading)
                st.markdown(value['text'])
            return
        if entry['kind'] == 'user':
            with st.chat_message('user'):
                st.caption(heading)
                st.markdown(value['text'])
                for asset in project.get('research_assets') or []:
                    if asset.get('asset_id') in value.get('attachment_ids', []):
                        st.caption('📎 ' + asset['filename'])
                statuses = {'queued': '已保存，等待处理', 'processing': '正在理解你的要求',
                    'saved': '已保存到后续研究上下文', 'applying': '正在处理这项要求',
                    'applied': '本次操作已完成，请查看下方结果', 'awaiting_confirmation': '等待你确认调整',
                    'declined': '未采用这项调整', 'superseded': '已由后续要求替代',
                    'failed': '这次处理未完成；输入已保留', 'not_applied': '尚未完成调整'}
                statuses['needs_input'] = ('Qwen 需要你补充信息，请在下方回答' if active_human_question(project)
                    else '当时提出的澄清问题，后续处理见下方讨论')
                statuses['clarified'] = '已收到补充，见后续回复'
                if value.get('notice') or value.get('status') in statuses:
                    st.caption(value.get('notice') or statuses[value['status']])
            return
        with st.chat_message('assistant'):
            reply_number = user_numbers.get(value.get('dialogue_message_id'))
            st.caption(heading + (f' · 回复你的 #{reply_number}' if reply_number else ' · 工作流进展'))
            phase = phase_labels.get(value.get('phase'), '研究讨论')
            turn = next((m for m in (project.get('human_dialogue') or {}).get('messages', [])
                         if m['id'] == value.get('dialogue_message_id')), {})
            if reading_state['text'] and value.get('public_response', '').strip() == reading_state['text']:
                st.caption(reading_state['label'])
            elif value.get('message_kind') == 'conversation_answer' and turn.get('action') in {'revise_answer', 'review_answer'}:
                st.caption('Qwen 对请求的理解（执行前说明）')
            else:
                st.caption(phase + (' · 生成中' if value.get('status') in {'started', 'working', 'streaming'} else ''))
            if value.get('status') in {'failure', 'failed', 'interrupted'}:
                st.warning('本次回复中断；以下为已收到的部分内容。')
            render_model_answer(backend_url, project, value.get('public_response', ''),
                value.get('figures', []), get_json, f"timeline_{project['project_id']}_{index}")
            if value.get('message_kind') == 'conversation_answer' and turn.get('status') == 'applied':
                st.success('实际处理结果：已更新研究回答并保存旧版。' if turn.get('action') == 'revise_answer'
                           else '实际处理结果：本次核查已返回，意见与状态见研究回答。')
            related = [o for o in observations if o.get('call_id') and o['call_id'] == value.get('call_id')]
            for obs in related:
                result = obs.get('result') or {}
                label = tool_view(obs, project.get('active_job_id'))
                with st.expander(label):
                    st.caption('模型行动意图（不代表执行结果）：' + (obs.get('purpose') or ''))
                    if obs.get('requested_url'):
                        st.write('请求地址：' + obs['requested_url'])
                    if result.get('new_chars') is not None:
                        st.caption(f"新增阅读字符：{result['new_chars']}")
                    if result.get('candidates'):
                        for candidate in result['candidates']:
                            st.write(candidate.get('title') or candidate.get('url'))
                            st.write(candidate.get('url'))
                            if candidate.get('snippet'):
                                st.caption(candidate['snippet'][:600])
                            st.caption(candidate.get('verification_note') or '检索线索，尚非已采用证据。')
                    for key in ('summary', 'message', 'error'):
                        if result.get(key):
                            st.write(result[key])
                    if result.get('url'):
                        st.write(result['url'])
                    if result.get('text'):
                        st.text(result['text'][:3000])
                        st.caption('原文节选；完整读取记录保存在研究档案。')
            if value.get('provider_responses'):
                with st.expander('模型调用记录'):
                    for receipt in value['provider_responses']:
                        st.caption(f"{receipt.get('response_model') or receipt.get('requested_model')} · "
                                   f"{receipt.get('response_id') or '未返回响应编号'}")

    old, current = timeline_partition(entries)
    if old:
        with st.expander(f'查看更早记录 · #1–#{len(old)} · {len(old)} 条'):
            for i in old:
                render(entries[i], i)
    for i in current:
        if i == len(entries) - 1:
            st.markdown('<div id="research-latest"></div>', unsafe_allow_html=True)
        render(entries[i], i)
    if reading_state['text'] and not has_answer:
        if reading_state['needs_revision']:
            st.caption('已有阅读草稿，但资料整理仍需修订；这不是研究完成。')
    if not entries and not reading_state['text']:
        st.caption('开始后，研究发现、你的补充和相关工具结果会依次显示在这里。')


PROPOSAL_EFFECTS = {
    'background_followup': '将在现有资料研究范围内继续处理；原有网络许可、额度和来源采用关卡保持不变。',
    'restart_sources': '保留旧回答与记录，重新提出资料研究范围。旧方案审批失效，联网仍需重新确认；不会直接执行实验。',
    'revise_plan': '退回对应规划阶段，原方案需重新审阅；不会执行代码。',
    'deliver_plan': '本轮交付资料解释和研究方案，不等待实测；确认后整理最终报告。不会把计划记为已执行实验。',
    'revise_code': '保存计算方案修改意见并撤回待批代码；不会执行，生成新代码后仍需审批。',
}


def render_composer(project, busy, submit, confirm, renew=None, save_files=None):
    st.markdown('<div id="research-composer"></div>', unsafe_allow_html=True)
    dialogue = project.get('human_dialogue') or {}
    proposal = dialogue.get('proposal')
    if proposal:
        with st.container(border=True):
            st.markdown('**需要你确认的调整**')
            st.write(PROPOSAL_EFFECTS.get(proposal['action'], '请核对这项调整。'))
            with st.expander('核对完整调整要求'):
                st.markdown(proposal.get('feedback', ''))
            if st.button('确认这项调整', disabled=busy, key='confirm_' + proposal['id']):
                confirm({'proposal_id': proposal['id'], 'accept': True})
            if st.button('暂不调整', disabled=busy, key='decline_' + proposal['id']):
                confirm({'proposal_id': proposal['id'], 'accept': False})
    pending = active_human_question(project)
    if pending:
        st.info(pending['prompt'])
    if busy:
        st.caption('可以先写下消息；请暂停并等待当前操作保存后再发送，输入不会被清空。')
    budget = project.get('budget') or {}
    exhausted = budget.get('attempted_model_calls', 0) >= budget.get('max_model_calls', float('inf'))
    if exhausted and renew:
        current = max(budget.get('max_model_calls', 0), budget.get('attempted_model_calls', 0))
        if current < 300:
            with st.expander('追加模型额度'):
                limit = st.number_input('模型请求总上限', min_value=current + 1, max_value=300,
                    value=min(current + 10, 300), key=f"dialogue_budget_{project['project_id']}")
                ack = st.checkbox('我同意新增请求可能产生的 API 费用', key=f"dialogue_budget_ack_{project['project_id']}")
                st.caption('只增加上限，不自动发送输入、启动研究或改变审批。')
                if st.button('确认追加额度', disabled=busy or not ack, key=f"dialogue_budget_submit_{project['project_id']}"):
                    renew({'max_model_calls': int(limit), 'acknowledged': True})
        else:
            st.info('已达到当前项目的 300 次请求上限。仍可阅读、下载已有成果。')
    key = f"composer_{project['project_id']}"
    # Do not clear on a rejected/failed request. The submit callback reruns ONLY after success.
    if st.session_state.pop(key + '_clear', False):
        st.session_state[key] = ''
        st.session_state[key + '_upload_version'] = st.session_state.get(key + '_upload_version', 0) + 1
    if st.session_state.get(key + '_notice'):
        st.success(st.session_state.pop(key + '_notice'))
    failed = next((m for m in reversed(dialogue.get('messages', []))
                   if m.get('status') in {'failed', 'not_applied'}), None)
    if failed and not busy:
        if st.button('重新编辑上次未完成的请求', key=key + '_retry'):
            st.session_state[key] = failed['text']
            st.session_state.pop(key + '_attempt', None)
    with st.form(key + '_form'):
        st.markdown('**发送消息与附件**')
        text = st.text_area('你的消息', key=key,
            placeholder='提问、回答模型的问题、提出修改要求，或说明附件希望怎样使用……')
        uploads, options = [], {'kind': 'reference', 'round': 1}
        if save_files:
            with st.expander('添加附件（可选）'):
                uploads = st.file_uploader('资料、数据或实验结果', accept_multiple_files=True,
                    type=['pdf', 'md', 'txt', 'csv', 'tsv', 'json', 'xml', 'xlsx', 'xls'],
                    key=key + '_files_' + str(st.session_state.get(key + '_upload_version', 0)), disabled=busy)
                st.caption(f"每次最多 20 个文件，每个不超过 {int(os.getenv('AI_SCIENTIST_MAX_ASSET_BYTES', str(25 * 1024 * 1024))) // (1024 * 1024)} MB；超限不会保存。")
                options['kind'] = st.radio('附件用途', ['reference', 'data', 'experimental_result'],
                    format_func=lambda v: {'reference': '参考资料', 'data': '待分析数据', 'experimental_result': '真实实验结果'}[v],
                    horizontal=True, key=key + '_kind')
                options['round'] = int(st.number_input('实验轮次（仅实验结果需要）', min_value=1,
                    value=max(1, int(project.get('iteration') or 0) + 1), key=key + '_round'))
                st.caption('附件随本次发送保存。只添加附件、不填文字时，仅保存文件，不调用模型。')
        if st.form_submit_button('发送并继续' if pending else '发送', disabled=busy,
                                 type='primary'):
            if exhausted and text.strip():
                st.warning('模型额度已用完。请先追加额度；文字和附件尚未提交。')
            elif not text.strip() and not uploads:
                st.warning('请填写消息或添加附件。')
            else:
                saved, errors = save_files(list(uploads or []), options, text) if uploads else ([], [])
                if errors:
                    for error in errors:
                        st.error(error)
                    st.warning(f'已保存 {len(saved)} 个附件；消息尚未发送。修正失败附件后重试，已确认保存的文件不会重复上传。')
                    return
                attachment_ids = [a['asset_id'] for a in saved]
                signature = hashlib.sha256((text.strip() + str(pending.get('request_id')) + str(attachment_ids)).encode()).hexdigest()
                attempt = st.session_state.get(key + '_attempt') or {}
                if attempt.get('signature') != signature:
                    attempt = {'signature': signature, 'id': uuid.uuid4().hex}
                    st.session_state[key + '_attempt'] = attempt
                submit({'message': text.strip(), 'message_id': attempt['id'],
                        'request_id': pending.get('request_id'), 'attachment_ids': attachment_ids}, key)
    st.caption('发送文字会调用 Qwen；只保存附件不调用模型。资料采用、方案批准和代码执行仍需明确确认。' if not exhausted
               else '模型额度已用完，输入仍保留；追加额度后可以发送。')
