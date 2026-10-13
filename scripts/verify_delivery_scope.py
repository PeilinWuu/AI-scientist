"""Two live Qwen routing checks, not a full workflow acceptance test."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase, ResearchQuestion


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', required=True, help='Explicitly permit two paid routing calls')
    args = parser.parse_args()
    root = Path('artifacts/delivery-scope-verification')
    o = ResearchOrchestrator(root)
    cases = [
        ('为什么城市树荫与遮阳棚下的凉爽程度不同？请解释机制，并设计校园比较实验。',
         '没有现场数据，这一轮只需要公开资料解释和一个以后可以执行的实验方案。', True),
        ('比较我公司的两种生产工艺对产品寿命的影响，计算均值差和置信区间。',
         '目前尚未上传测量数据，希望拿到实际数据分析的结果；不要用模拟数据替代。', False),
    ]
    results = []
    for objective, constraints, expected in cases:
        p = o.create_project(objective, constraints_text=constraints, planning_only=False, research_policy_version=2)
        # Isolate the routing stage with the exact user input; no claim of completing intake.
        p.question = ResearchQuestion(original_question=objective, normalized_question=objective)
        p.phase = ResearchPhase.RESEARCH_MODE_SELECTION
        o.store.save(p)
        o.run_next_step(p.project_id)
        p = o.get_project(p.project_id)
        results.append({'project_id': p.project_id, 'expected_planning_only': expected,
                        'actual_planning_only': p.planning_only,
                        'research_mode': p.research_mode.value if p.research_mode else None,
                        'pending_input': p.human_dialogue.get('pending'),
                        'decision': p.constraints.get('delivery_scope_decision'),
                        'passed': p.planning_only == expected})
    print(json.dumps(results, ensure_ascii=False, indent=2))
    if not all(result['passed'] for result in results):
        raise SystemExit(1)
