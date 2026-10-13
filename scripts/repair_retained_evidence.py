"""Explicit, offline repair of a lost adopted evidence set; dry-run by default.

Stop the API before --apply. Specify registered artifact filenames from the same
project, never arbitrary input paths. This restores records, not scientific approval.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.ai_scientist.background_agent import BackgroundAgent
from src.ai_scientist.job_store import ResearchJobStore
from src.ai_scientist.orchestrator import ResearchOrchestrator
from src.ai_scientist.research_context import preserve_adopted_read_receipts
from src.ai_scientist.schemas import EvidenceItem, ResearchPhase


def repair(o, project_id, evidence_file, archive_file, *, apply=False):
    if not re.fullmatch(r'project_[a-zA-Z0-9]+', project_id):
        raise ValueError('Invalid project id')
    root = o.store.root.resolve()
    directory = root / project_id
    if directory.is_symlink() or directory.resolve().parent != root:
        raise ValueError('Project must be a direct, non-symlink child')
    if (directory / 'artifacts').is_symlink():
        raise ValueError('Artifact directory must not be a symlink')
    p = o.store.load(project_id)
    if p.active_job_id or ResearchJobStore(root).active_step_job(project_id):
        raise ValueError('Wait until the project job has stopped')
    if p.evidence or p.phase not in {ResearchPhase.EXECUTION_WAITING, ResearchPhase.HUMAN_APPROVAL}:
        raise ValueError('Repair is restricted to an empty evidence set in an unfinished delivery')
    if not p.background_agent.get('basis_approval', {}).get('without_external_sources'):
        raise ValueError('No empty-adoption record found; inspect manually')

    def read_registered(filename, kind):
        record = next((a for a in p.artifacts if a.filename == filename and a.artifact_type == kind), None)
        if not record or Path(filename).name != filename:
            raise ValueError('Artifact must be registered in this project')
        path = directory / 'artifacts' / filename
        if path.is_symlink() or path.resolve().parent != (directory / 'artifacts').resolve():
            raise ValueError('Unsafe artifact path')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != record.checksum:
            raise ValueError('Artifact checksum mismatch')
        return json.loads(raw), record

    evidence, evidence_record = read_registered(evidence_file, 'evidence_map')
    archive, archive_record = read_registered(archive_file, 'background_rollback_archive')
    if not archive.get('basis_approval') or not archive.get('adopted_source_ids'):
        raise ValueError('Archive has no original human adoption')
    restored = [EvidenceItem.model_validate(item) for item in evidence.get('evidence', [])]
    if not restored:
        raise ValueError('Selected evidence artifact is empty')
    adopted_urls = {s.get('url') for s in archive.get('sources', [])
                   if s.get('candidate_id') in archive['adopted_source_ids']}
    if any(not e.source_url or e.source_url not in adopted_urls for e in restored):
        raise ValueError('Evidence does not match originally adopted sources')
    candidate = p.model_copy(deep=True)
    candidate.evidence, candidate.background_agent = restored, archive
    preserve_adopted_read_receipts(candidate)
    if any(not e.retrieval_provenance.get('read_receipt') for e in candidate.evidence):
        raise ValueError('Original read receipts cannot be recovered for every source')
    result = {'project_id': project_id, 'restored_count': len(restored),
              'evidence_file': evidence_file, 'archive_file': archive_file,
              'applied': apply, 'next_phase': 'CLAIM_EVIDENCE_MAPPING'}
    if not apply:
        return result
    service = BackgroundAgent(o)
    service.record(p, 'before_evidence_repair', p.model_dump(mode='json'))
    p.evidence = candidate.evidence
    evidence_id = service.record(p, 'evidence_map', {'evidence': [e.model_dump(mode='json') for e in p.evidence]})
    p.active_artifact_versions['evidence'] = next(a.version for a in p.artifacts if a.artifact_id == evidence_id)
    p.background_agent['basis_approval'].update(without_external_sources=False,
        recovered_evidence_ids=[e.evidence_id for e in p.evidence])
    note = ('系统修复恢复了此前由用户采用、在补充检索中意外清空的资料及原文片段。'
            '请依据恢复的原文重新核对现有方案与来源归因；旧对话和旧摘要不是证据。'
            '本次恢复不是新实验，也不等于完成研究。')
    p.revision_feedback.append(note)
    p.human_dialogue.pop('proposal', None)
    p.conclusion = None
    o._invalidate_approval(p, note)
    p.phase = ResearchPhase.CLAIM_EVIDENCE_MAPPING
    service.record(p, 'evidence_repair', {**result, 'original_evidence_artifact': evidence_record.artifact_id,
                                       'original_archive_artifact': archive_record.artifact_id})
    _register_link_repair(o, p)
    p.stage_messages.append(note)
    o.store.save(p)
    return result


def _register_link_repair(o, p):
    record_id = BackgroundAgent(o).record(p, 'evidence_link_repair', {
        'evidence_version': p.active_artifact_versions.get('evidence'),
        'reason': 'Rebuild lost claim links from recovered original passages, then independently review.'})
    p.human_dialogue['evidence_link_repair'] = {
        'repair_artifact_id': record_id, 'evidence_version': p.active_artifact_versions.get('evidence')}


def refresh_recovered_links(o, project_id, *, apply=False):
    """Complete an earlier recovery which restored sources but omitted claim links."""
    if not re.fullmatch(r'project_[a-zA-Z0-9]+', project_id):
        raise ValueError('Invalid project id')
    p = o.store.load(project_id)
    if p.active_job_id or ResearchJobStore(o.store.root).active_step_job(project_id):
        raise ValueError('Wait until the project job has stopped')
    if (not p.evidence or not any(a.artifact_type == 'evidence_repair' for a in p.artifacts)
            or not p.planning_only or p.phase not in {ResearchPhase.COMPLETED, ResearchPhase.HUMAN_APPROVAL}
            or p.internal_execution_summary or p.controlled_python_runs):
        raise ValueError('Only a recovered, unexecuted planning project can use this repair')
    result = {'project_id': project_id, 'applied': apply, 'next_phase': 'CLAIM_EVIDENCE_MAPPING'}
    if apply:
        BackgroundAgent(o).record(p, 'before_evidence_link_repair', p.model_dump(mode='json'))
        _register_link_repair(o, p)
        p.conclusion = None
        o._invalidate_approval(p, '恢复的原文需要重建主张关联，之后重新审核，而非直接认定结论成立。')
        p.phase = ResearchPhase.CLAIM_EVIDENCE_MAPPING
        o.store.save(p)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    parser.add_argument('--evidence')
    parser.add_argument('--archive')
    parser.add_argument('--refresh-links', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if not args.refresh_links and (not args.evidence or not args.archive):
        parser.error('--evidence and --archive are required for evidence restoration')
    result = (refresh_recovered_links(ResearchOrchestrator(), args.project, apply=args.apply)
              if args.refresh_links else repair(ResearchOrchestrator(), args.project, args.evidence,
                                                args.archive, apply=args.apply))
    print(json.dumps(result, ensure_ascii=False, indent=2))
