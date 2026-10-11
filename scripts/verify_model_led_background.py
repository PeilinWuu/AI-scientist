"""Opt-in paid live smoke test, isolated from user projects and approvals.

Run from repository root: .venv/bin/python scripts/verify_model_led_background.py --live --case source
The script approves ONLY its own temporary test project. It never approves scientific results.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_scientist.background_agent import BackgroundAgent
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Allow paid calls with the locally configured Qwen API')
    parser.add_argument('--case', choices=['math', 'source'], default='math')
    args = parser.parse_args()
    if not args.live:
        parser.error('--live is required; this test makes real paid API calls')
    root = tempfile.mkdtemp(prefix='scientist-background-acceptance-')
    o = ResearchOrchestrator(root)
    objective = {
        'math': '求解 dx/dt=-a*x，x(0)=5，在 t=0..8 对比 a=0.3 与 0.8 的数值解和解析解，计算半衰期及最大误差。只使用给定数学前提，不引入经验断言。',
        'source': '查阅 SciPy 官方 solve_ivp 的误差控制说明，判断 rtol 和 atol 如何控制局部误差、哪些情况还须检验数值结果。必须读取官方文档原文作为依据，本阶段只做资料研究，不运行代码。',
    }[args.case]
    p = o.create_project(objective, research_policy_version=2)
    p.phase = ResearchPhase.BACKGROUND_RESEARCH
    p.budget.max_model_calls = 24
    o.store.save(p)
    s = BackgroundAgent(o)
    print(json.dumps({'isolated_project': p.project_id, 'audit_root': root, 'case': args.case}, ensure_ascii=False), flush=True)
    s.advance(p)
    assert p.phase == ResearchPhase.SEARCH_PLAN_REVIEW and not p.background_agent.get('authorization')
    print(json.dumps({'scope': p.background_agent['scope'], 'gate': p.phase.value}, ensure_ascii=False), flush=True)
    # This consent is solely the explicit test harness, not a user's project or approval.
    s.authorize(p, p.background_agent['scope_id'], allow_network=args.case == 'source', action_budget=12)
    for _ in range(2):
        s.advance(p)
        print(json.dumps({'phase': p.phase.value, 'status': p.background_agent['status'],
                          'actions': [{'action': x['action'], 'status': x['status'], 'result_status': x['result'].get('status'),
                                       'error': x['result'].get('error')} for x in p.background_agent['observations']]}, ensure_ascii=False), flush=True)
        if p.phase != ResearchPhase.BACKGROUND_RESEARCH:
            break
    if p.background_agent['status'] != 'awaiting_adoption':
        raise RuntimeError('Test did not reach an adoption proposal; inspect the retained audit directory')
    assert not p.evidence
    ids = [sid for sid, a in p.background_agent['assessments'].items() if a['decision'] == 'keep' and a['quote_matched']]
    if args.case == 'source':
        assert ids, 'No read and assessed source was proposed'
    s.adopt(p, ids, confirm_basis=args.case == 'math', scope_id=p.background_agent['scope_id'],
            proposal_id=p.background_agent['proposal_id'])
    result = o.run_next_step(p.project_id)
    p = o.get_project(p.project_id)
    assert p.phase == ResearchPhase.HYPOTHESIS_GENERATION, result
    if args.case == 'math':
        assert all(c.status not in {'supported', 'partially_supported'} for c in p.claims)
    print(json.dumps({'result':'passed', 'phase':p.phase.value, 'evidence':len(p.evidence), 'claims':len(p.claims),
                      'calls':p.budget.attempted_model_calls, 'audit_root':root,
                      'meaning':'Background and claim-mapping mechanics only; no completed scientific experiment claimed.'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
