"""User-visible progress: describe work, not transport counters."""


def approval_summary(project):
    """Summarize recorded decisions without generating or certifying new claims."""
    design = project.get('study_design') or {}
    analysis = project.get('analysis_plan') or {}
    entries = [('本轮要做', design.get('objective') or analysis.get('objectives')),
               ('如何开展', design.get('sampling_plan')),
               ('交付内容', analysis.get('visualization_plan')),
               ('停止条件', design.get('stopping_rules')),
               ('主要局限', design.get('risks'))]
    if project.get('planning_only'):
        entries = [('本轮交付', '资料解释与研究方案；不开展新的实测或计算，也不等待实验数据'),
                   ('研究目标', design.get('objective') or project.get('objective')),
                   ('未来实验建议', design.get('sampling_plan')),
                   ('主要局限', design.get('risks'))]
    result = []
    for label, values in entries:
        if values:
            result.append((label, '；'.join(str(v) for v in values[:3]) if isinstance(values, list) else str(values)))
    return result or [('研究目标', project.get('objective', '请展开方案查看'))]


def model_activity(progress):
    roles = {'skeptical_reviewer': '核对回答与资料', 'scientific_synthesizer': '整理研究回答',
             'evidence_researcher': '阅读与整理研究依据', 'study_designer': '设计研究方案',
             'analyst': '制定分析方法', 'hypothesis_generator': '比较可能的解释'}
    action = roles.get(progress.get('agent_name'), '处理当前研究任务')
    elapsed = progress.get('elapsed_seconds')
    return action + (f' · 本次调用 {elapsed:.0f} 秒' if elapsed is not None else '')


def failure_explanation(error, project):
    message = str(error.get('error_message') or '')
    if error.get('stage_substep') == 'audit_protocol' or '审核意见自身未通过原文校验' in message:
        return '上次补充核查没有完成：审核模型的引文未能定位到原文。草稿和资料已保留，请直接阅读下方回答；可按需重新核查或提出修改，不需要重新创建项目。'
    if error.get('error_type') == 'worker_restarted':
        return '上次运行被服务重启中断，当前没有在后台继续。已保存的回答和资料仍可查看；需要时从当前操作恢复。'
    if (project.get('literature_review') or {}).get('review_status') == 'unavailable':
        return '补充核查暂未完成，研究回答仍可阅读、下载和修改。可稍后重试核查，不必重跑资料检索。'
    return ''
