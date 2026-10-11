"""Live Qwen acceptance of the computation subsystem, not a full workflow test.

Creates explicit human-approved computational fixtures and calls the SAME service
used by the frontend. Never patches model outputs or supplies solution code.
"""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_scientist.computation import ComputationService
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.schemas import ResearchPhase


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--case', choices=['simulation', 'missing-data', 'table'], required=True)
    args = parser.parse_args()
    os.environ['AI_SCIENTIST_ENABLE_COMPUTATION'] = '1'
    questions = {
        'simulation': 'Numerically simulate dx/dt = r*x*(1-x/K), x(0)=3, K=80, over t=0..25 for r=0.15 and r=0.45. Compare the time to reach x=40. Save a trajectory plot and CSV. Validate the numerical trajectory against the analytic logistic solution and report maximum absolute error. This is explicitly a mathematical simulation, not observed data. One round suffices if validation passes.',
        'missing-data': 'Compare average recovery time between treated and untreated patients from my clinical dataset. No dataset has been uploaded. Do not invent or simulate patient data.',
        'table': 'Using the uploaded synthetic classroom exercise dataset, compare score between groups A and B. Compute group sizes, means, B-minus-A mean difference and a bootstrap 95% interval with seed 4711. Save a plot and group summary CSV. Make no causal claim. The score column is the outcome; record_id is only an identifier. One validated round is enough.',
    }
    o = ResearchOrchestrator(Path(args.output))
    project = o.create_project(questions[args.case], planning_only=False, reproducibility_seed=4711)
    if args.case == 'table':
        import numpy as np
        rng = np.random.default_rng(823)
        rows = ['record_id,group,score']
        for i in range(80):
            group = 'A' if i < 40 else 'B'
            score = 50 + (5 if group == 'B' else 0) + rng.normal(0, 8)
            rows.append(f'{i},{group},{score:.6f}')
        project = o.register_research_asset(project.project_id, 'classroom.csv', 'text/csv',
                                           ('\n'.join(rows)+'\n').encode(), purpose='data',
                                           source='explicitly synthetic acceptance fixture')
    # Transparent test fixture: bypasses literature planning, NOT computation approval.
    project.phase = ResearchPhase.EXECUTION_WAITING
    project.approval_status = 'valid'
    project.stage_messages.append('Acceptance fixture: computation subsystem only; upstream planning was not run.')
    o.store.save(project)
    print(json.dumps({'project_id': project.project_id, 'case': args.case}), flush=True)
    service = ComputationService(o)
    pending = service.prepare(project.project_id)
    print(json.dumps({'plan_decision': pending['plan']['decision'], 'method': pending['plan']['method']}), flush=True)
    if args.case == 'missing-data':
        assert pending['plan']['decision'] == 'needs_input', pending['plan']['decision']
        print('PASS: missing input reported without invented results', flush=True)
        return
    assert pending['status'] == 'awaiting_approval', pending['status']
    result = service.execute(project.project_id, pending['plan_id'])
    print(json.dumps({'status': result['status'], 'attempts': [a['status'] for a in result['attempts']],
                      'assessment': result['rounds'][-1]['assessment'] if result['rounds'] else None}, ensure_ascii=False), flush=True)
    assert result['rounds'], result
    assert result['rounds'][-1]['attempts'][-1]['status'] == 'success'
    print('PASS: Qwen generated and isolated Python executed', flush=True)


if __name__ == '__main__':
    main()
