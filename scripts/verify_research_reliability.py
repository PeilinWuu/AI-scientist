"""Opt-in live regression using a saved source; never changes the original project.

Run from repository root with PYTHONPATH=. and --live. Output contains receipts,
not API credentials. This is a source-faithfulness probe, not end-to-end acceptance.
"""
import argparse
import json
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--live', action='store_true', help='Authorize real paid model calls, including bounded format repair')
    parser.add_argument('--case', choices=['all', 'positive', 'negative'], default='all')
    parser.add_argument('--synthesis-only', action='store_true', help='Live component test with a temporary approved/adopted fixture, not UI acceptance')
    parser.add_argument('--resume-attempts', type=int, choices=[0, 1], default=0,
                        help='Optionally exercise one resume after a rejected draft, preserving its correction feedback')
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to explicitly enable real model requests')
    import src.config  # Load the existing local configuration without printing it.
    from src.ai_scientist.schemas import ResearchProject
    from src.ai_scientist.background_agent import BackgroundAgent, SourceAssessmentReview, assessment_passages
    from src.ai_scientist.orchestrator import ResearchOrchestrator
    from src.ai_scientist.model_progress import model_progress
    project = ResearchProject.model_validate_json(args.project.read_text())
    source = next(s for s in project.background_agent['sources'] if 'PMC10278514' in s.get('url', ''))
    passages = assessment_passages(source, '')
    cases = [
        ('overclaim_must_be_rejected', False,
         '针灸比假针灸显著改善所有化疗相关恶心呕吐结局，证据确定性高。'),
        ('bounded_finding_should_be_supported', True,
         '该综述提示在常规护理基础上加用针灸可能改善部分呕吐结局，但证据确定性很低。不能据此声称对所有恶心呕吐结局或假针灸对照均有优势，也不能证明经络是独立解剖实体。'),
    ]
    results = []
    with tempfile.TemporaryDirectory(prefix='scientist-live-audit-') as temp:
        orchestrator = ResearchOrchestrator(temp)
        project.active_job_id = None
        project.budget.max_model_calls = project.budget.attempted_model_calls + 16
        orchestrator.store.save(project)
        agent = BackgroundAgent(orchestrator)
        if args.synthesis_only:
            from src.ai_scientist.schemas import EvidenceItem, ResearchMode, ResearchPhase
            from src.ai_scientist.literature_execution import run_review
            # Explicit component-test fixture. No production adoption or approval
            # is written; never present this as human-completed frontend workflow.
            project.objective = '仅依据给定综述，比较不同对照组下的治疗效果和证据确定性，形成限定范围的证据表与短综述。不要新增实验或把它称为全面系统综述。'
            project.question = None
            project.constraints = {'scope': '单篇已读综述的证据综合，明确局限，不推广到其他领域。'}
            project.research_mode = ResearchMode.SYSTEMATIC_REVIEW
            project.planning_only = False
            project.approval_status = 'valid'
            project.approval_valid_for_versions = orchestrator._review_artifact_versions(project)
            project.phase = ResearchPhase.EXECUTION_WAITING
            project.evidence = [EvidenceItem(title=source['title'], source_type='web_source', source_url=source['url'],
                summary='用于单篇综述证据提取组件测试；结论以实际原文为准', source_quote='')]
            project.background_agent = {'sources': [source], 'scope': {'external_information': 'required',
                'rationale': 'A single-source bounded synthesis fixture'}, 'observations': [], 'assessments': {}}
            project.human_dialogue = {}
            progress = []
            with model_progress(progress.append):
                for attempt in range(args.resume_attempts + 1):
                    try:
                        run_review(orchestrator, project)
                        break
                    except ValueError as exc:
                        print(json.dumps({'attempt': attempt + 1,
                            'status': project.literature_review.get('status'), 'error': str(exc)}, ensure_ascii=False), flush=True)
                        if (attempt == args.resume_attempts or
                                project.literature_review.get('status') != 'needs_revision'):
                            raise
            print(json.dumps({'test_type': 'live_component_fixture_not_frontend_acceptance',
                'status': project.literature_review.get('status'), 'rows': project.literature_review.get('rows'),
                'report': project.literature_review.get('report_markdown'),
                'audit': project.literature_review.get('audit'),
                'python_executed': bool(project.controlled_python_runs or project.computation),
                'calls': [{'model': e.get('model'), 'usage': e.get('token_usage'), 'receipts': e.get('provider_responses')}
                          for e in progress if e.get('status') == 'completed']}, ensure_ascii=False), flush=True)
            if project.literature_review.get('status') != 'complete':
                raise SystemExit(1)
            return
        for name, expected, claim in cases:
            if args.case != 'all' and expected != (args.case == 'positive'):
                continue
            progress = []
            with model_progress(progress.append):
                result = agent.call(project, SourceAssessmentReview, {
                    'proposed_assessment': {'summary': claim}, 'actual_read_passages': passages,
                    'source': {'title': source['title'], 'url': source['url']},
                }, role='skeptical_reviewer')
            completed = [e for e in progress if e.get('status') == 'completed']
            results.append({'case': name, 'passed': result.supported == expected,
                            'review': result.model_dump(),
                            'calls': [{'model': e.get('model'), 'usage': e.get('token_usage'),
                                       'receipts': e.get('provider_responses')} for e in completed]})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
    if not all(r['passed'] for r in results):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
