"""Display actual tool state independently of the model's stated intention."""
from urllib.parse import urlsplit


def tool_view(observation, active_job_id=None):
    result = observation.get('result') or {}
    status = observation.get('status')
    active = bool(active_job_id and observation.get('job_id') == active_job_id)
    if status == 'running' or (status == 'interrupted' and active and not observation.get('finished_at')):
        label = '正在执行工具' if active else '工具已中断，尚无完成记录'
    elif status == 'failed':
        label = '工具执行失败'
    elif result.get('status') in {'phrase_not_found', 'no_new_sources', 'tool_not_executed', 'proposal_requires_revision'}:
        label = '本次未取得所需内容'
    elif result.get('new_chars') == 0:
        label = '已返回已读片段，没有新增内容'
    elif status == 'completed':
        label = '工具已返回'
    else:
        label = '行动未完成'
    action = {'read_source': '读取网页', 'search_web': '搜索公开网页', 'search_papers': '检索论文元信息',
              'assess_source': '评估资料', 'finish': '整理阅读发现'}.get(observation.get('action'), observation.get('action', ''))
    url = result.get('url') or observation.get('requested_url') or ''
    target = urlsplit(url).netloc if url else observation.get('source_id', '')
    return f'{label} · {action}' + (f' · {target}' if target else '')
