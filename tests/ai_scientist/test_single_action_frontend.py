"""Exercise the real Streamlit page using an entirely in-memory backend.

These are navigation regressions, not model/science acceptance tests. No request
may reach the running local project or an external model provider.
"""
from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import requests
from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[2] / 'app_streamlit.py'
PID = 'project_single_action_ui'
PREFIX = f'/api/research/{PID}'


def project_snapshot(**changes):
    project = {
        'project_id': PID,
        'objective': '研究异常值对均值和中位数的影响。',
        'phase': 'QUESTION_FORMULATION',
        'planning_only': False,
        'research_policy_version': 2,
        'execution_capability': 'EXTERNAL_EXECUTION_REQUIRED',
        'available_tools': ['isolated_scientific_python'],
        'approval_status': 'valid',
        'active_job_id': None,
        'iteration': 0,
        'budget': {'used_model_calls': 0, 'max_model_calls': 30},
        'quality_metrics': {},
        'research_assets': [],
        'evidence': [],
        'artifacts': [],
        'computation': {},
        'human_dialogue': {},
    }
    project.update(changes)
    return project


class MemoryResponse:
    ok = True
    status_code = 200
    text = '# 测试研究计划\n仅用于前端回归测试。'
    content = text.encode()

    def __init__(self, value, *, status_code=200):
        self.value = value
        self.status_code = status_code
        self.ok = status_code < 400

    def json(self):
        return deepcopy(self.value)


class MemoryBackend:
    def __init__(self, project, job=None):
        self.project = deepcopy(project)
        self.jobs = {job['job_id']: deepcopy(job)} if job else {}
        self.posts = []
        self.fail_continue_count = 0
        self.continue_transport_error = None
        self.get_transport_errors = {}
        self.messages = {}

    def get(self, url, **kwargs):
        path = urlsplit(url).path
        if path in self.get_transport_errors:
            raise self.get_transport_errors[path]
        if path == '/health':
            value = {'general_computation_enabled': True}
        elif path == PREFIX:
            value = self.project
        elif path.startswith('/api/research/jobs/'):
            value = self.jobs[path.rsplit('/', 1)[-1]]
        elif path.endswith('/conversation'):
            value = {'messages': []}
        elif path.endswith(('/events', '/artifacts', '/research-assets')):
            value = []
        elif path.endswith('/review-package'):
            value = {'package_id': 'package_ui', 'ready_for_approval': True,
                     'artifact_versions': {'question': 1}}
        else:
            value = {}
        return MemoryResponse(value)

    def post(self, url, *, json=None, **kwargs):
        path = urlsplit(url).path
        self.posts.append((path, deepcopy(json)))
        if path == PREFIX + '/message':
            if json['message_id'] in self.messages:
                return MemoryResponse(self.messages[json['message_id']])
            if self.fail_continue_count:
                self.fail_continue_count -= 1
                if self.continue_transport_error is not None:
                    raise self.continue_transport_error
                return MemoryResponse({'detail': '模拟消息服务暂时不可用'}, status_code=503)
            dialogue = self.project['human_dialogue']
            pending = dialogue.pop('pending', None)
            if pending:
                dialogue.setdefault('responses', []).append({**pending, 'response': json['message']})
            job = {'job_id': f'job_ui_{len(self.posts)}', 'project_id': PID,
                   'phase': self.project['phase'], 'status': 'completed', 'result': {}}
            self.jobs[job['job_id']] = job
            self.messages[json['message_id']] = job
            return MemoryResponse(job)
        if path == PREFIX + '/reply':
            dialogue = self.project['human_dialogue']
            pending = dialogue.pop('pending')
            dialogue.setdefault('responses', []).append({**pending, **json})
            return MemoryResponse(self.project)
        if path == PREFIX + '/background-agent/authorize':
            self.project['phase'] = 'BACKGROUND_RESEARCH'
            self.project['background_agent']['status'] = 'authorized'
            return MemoryResponse(self.project)
        if path == PREFIX + '/approve':
            self.project['phase'] = 'EXECUTION_WAITING'
            self.project['approval_status'] = 'valid'
            return MemoryResponse(self.project)
        if path == PREFIX + '/continue_async' and self.fail_continue_count:
            self.fail_continue_count -= 1
            if self.continue_transport_error is not None:
                raise self.continue_transport_error
            return MemoryResponse({'detail': {'error_message': '模拟排队服务暂时不可用'}},
                                  status_code=503)
        if path in {PREFIX + '/continue_async', PREFIX + '/step_async',
                    PREFIX + '/computation/prepare',
                    PREFIX + '/computation/resume_assessment'}:
            # Complete immediately so AppTest never enters polling/sleep loops.
            job = {'job_id': f'job_ui_{len(self.posts)}', 'project_id': PID,
                   'phase': self.project['phase'], 'status': 'completed',
                   'result': {'pause_reason': 'user_pause'}}
            self.jobs[job['job_id']] = job
            return MemoryResponse(job)
        raise AssertionError(f'Unexpected mutation in frontend test: {path}')


@pytest.fixture
def frontend(monkeypatch):
    def fail_network(*args, **kwargs):
        raise AssertionError('Unmocked network request is prohibited in frontend regression tests')

    monkeypatch.setattr('requests.sessions.Session.request', fail_network)

    def build(project=None, *, job=None, backend_project=None):
        snapshot = project if project is not None else project_snapshot()
        backend = MemoryBackend(snapshot, job)
        if backend_project is not None:
            backend.project = deepcopy(backend_project)
        monkeypatch.setattr('requests.get', backend.get)
        monkeypatch.setattr('requests.post', backend.post)
        app = AppTest.from_file(APP)
        app.session_state['research_project_id'] = PID
        app.session_state['research_project'] = deepcopy(snapshot)
        if job:
            app.session_state['research_job_id'] = job['job_id']
        app.run(timeout=20)
        assert not app.exception
        return app, backend

    return build


def labels(app):
    return [button.label for button in app.button]


def button(app, label):
    matches = [item for item in app.button if item.label == label]
    assert len(matches) == 1, (label, labels(app))
    return matches[0]


def assert_no_legacy_workflow_buttons(app):
    assert not any('运行下一阶段' in label for label in labels(app))
    assert '继续研究，直到需要我操作' not in labels(app)
    assert '生成计算计划与代码' not in labels(app)
    assert '仅运行一个阶段（调试）' not in labels(app)


def test_ordinary_stage_has_one_continue_and_default_hides_raw_json(frontend):
    app, backend = frontend()

    assert not button(app, '继续研究').disabled
    assert len([item for item in app.button if (item.key or '').startswith('next_action_')]) == 1
    assert_no_legacy_workflow_buttons(app)
    assert not app.json
    assert backend.posts == []


def test_continue_uses_continuous_endpoint_not_single_step(frontend):
    app, backend = frontend()

    button(app, '继续研究').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [(PREFIX + '/continue_async', None)]


def test_pending_question_replaces_continue_and_does_not_duplicate_reply(frontend):
    project = project_snapshot(human_dialogue={'pending': {
        'request_id': 'question_ui', 'prompt': '请确认使用模拟数据还是真实数据。',
        'phase': 'QUESTION_FORMULATION',
    }})
    app, backend = frontend(project)

    assert not button(app, '发送并继续').disabled
    assert '继续研究' not in labels(app)
    assert len([item for item in app.text_area if item.label == '你的消息']) == 1
    assert_no_legacy_workflow_buttons(app)
    assert backend.posts == []


def test_question_takes_priority_over_previously_generated_code_approval(frontend):
    app, backend = frontend(project_snapshot(
        phase='EXECUTION_WAITING',
        computation={'status': 'awaiting_approval', 'plan_id': 'stale_or_pending_plan'},
        human_dialogue={'pending': {'request_id': 'code_question',
                                   'prompt': '请先确认数据列的含义。'}},
    ))

    assert not button(app, '发送并继续').disabled
    assert '批准并执行计算' not in labels(app)
    assert '继续研究' not in labels(app)
    assert backend.posts == []


def test_reply_is_saved_exactly_then_continues_without_implicit_approval(frontend):
    project = project_snapshot(human_dialogue={'pending': {
        'request_id': 'question_ui', 'prompt': '请确认模拟条件。',
        'phase': 'QUESTION_FORMULATION',
    }})
    app, backend = frontend(project)
    reply = '  仅数学模拟；n=100，随机种子 42。\n不要据此批准联网或代码执行。  '
    next(item for item in app.text_area if item.label == '你的消息').set_value(reply)

    button(app, '发送并继续').click().run(timeout=20)

    assert not app.exception
    assert len(backend.posts) == 1 and backend.posts[0][0] == PREFIX + '/message'
    assert backend.posts[0][1]['message'] == reply.strip()
    assert backend.posts[0][1]['request_id'] == 'question_ui'
    assert 'pending' not in backend.project['human_dialogue']
    assert '发送并继续' not in labels(app)


def test_blank_reply_neither_saves_nor_starts_work(frontend):
    app, backend = frontend(project_snapshot(human_dialogue={'pending': {
        'request_id': 'question_ui', 'prompt': '请选择数据来源。',
    }}))

    button(app, '发送并继续').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == []
    assert any('请填写消息或添加附件' in item.value for item in app.warning)


def test_existing_project_has_one_composer_and_one_upload_widget(frontend):
    app, _ = frontend(project_snapshot())
    assert not app.exception
    assert [field.label for field in app.text_area] == ['你的消息']
    assert len(app.get('file_uploader')) == 1
    assert not any(button.label == '保存到项目' for button in app.button)


def test_failed_job_id_is_not_busy_and_retry_uses_same_project(frontend):
    job = {'job_id': 'job_failed_ui', 'project_id': PID,
           'phase': 'QUESTION_FORMULATION', 'status': 'failed',
           'error': {'error_type': 'APITimeoutError', 'error_message': 'request timeout'}}
    app, backend = frontend(job=job)

    assert app.session_state['research_job_id'] == job['job_id']
    assert not button(app, '重试当前研究阶段').disabled
    button(app, '重试当前研究阶段').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [(PREFIX + '/continue_async', None)]
    assert app.session_state['research_project_id'] == PID


def test_bound_execution_requires_explicit_confirmation_and_uses_single_step(frontend):
    app, backend = frontend(project_snapshot(
        phase='EXECUTION', execution_capability='INTERNAL_EXECUTABLE',
        executor_binding='damped_oscillator_v1'))

    assert '继续研究' not in labels(app)
    assert backend.posts == []
    button(app, '确认并开始项目内执行').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [(PREFIX + '/step_async', None)]


def test_saved_results_only_resume_assessment_never_execute(frontend):
    app, backend = frontend(project_snapshot(
        phase='EXECUTION_WAITING', computation={
            'status': 'needs_input', 'awaiting_assessment': {'run_id': 'saved_run'},
        }))

    assert '继续研究' not in labels(app)
    assert '批准并执行计算' not in labels(app)
    assert '生成计算方案' not in labels(app)
    button(app, '继续评估已有结果').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [(PREFIX + '/computation/resume_assessment', {})]


def test_computation_preparation_has_only_one_control(frontend):
    app, backend = frontend(project_snapshot(phase='EXECUTION_WAITING'))

    assert '继续研究' not in labels(app)
    assert_no_legacy_workflow_buttons(app)
    button(app, '生成计算方案').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [(PREFIX + '/computation/prepare', {})]


@pytest.mark.parametrize('view', ['研究档案 · 来源、方案与历史产物', '运行记录与技术详情'])
def test_navigation_preserves_single_action_and_keeps_raw_json_hidden(frontend, view):
    app, backend = frontend()

    assert any(item.label == view for item in app.expander)
    assert not any(item.label == '查看研究内容' for item in app.radio)
    app.run(timeout=20)

    assert not app.exception
    assert not button(app, '继续研究').disabled
    assert_no_legacy_workflow_buttons(app)
    assert not app.json
    assert backend.posts == []


def test_details_navigation_does_not_duplicate_computation_approval(frontend):
    app, backend = frontend(project_snapshot(
        phase='EXECUTION_WAITING', computation={
            'status': 'awaiting_approval', 'plan_id': 'plan_ui',
            'plan': {'rationale': '比较均值与中位数。', 'method': '数学模拟',
                     'code': 'print("test-only")', 'required_metrics': ['bias'],
                     'validation_checks': ['验证输出维度'], 'stopping_rule': '一次运行'},
        }))

    assert button(app, '批准并执行计算').disabled
    assert any(item.label == '研究档案 · 来源、方案与历史产物' for item in app.expander)
    app.run(timeout=20)

    assert not app.exception
    assert button(app, '批准并执行计算').disabled
    assert len([item for item in app.checkbox if item.key == 'computation_approve_plan_ui']) == 1
    assert '继续研究' not in labels(app)
    assert_no_legacy_workflow_buttons(app)
    assert not app.json
    assert backend.posts == []


def test_completed_main_action_opens_results_with_one_report_download(frontend):
    app, backend = frontend(project_snapshot(phase='COMPLETED'))

    assert '继续研究' not in labels(app)
    assert '查看研究成果' not in labels(app)  # Results no longer require a navigation action.

    assert not app.exception
    assert not any(item.label == '查看研究内容' for item in app.radio)
    report_downloads = [item for item in app.get('download_button')
                        if item.proto.label == '下载研究计划']
    assert len(report_downloads) == 1
    assert any(item.value == '研究成果与局限' for item in app.subheader)
    assert backend.posts == []


def test_basis_approval_saves_exact_scope_then_continues_without_execution(frontend):
    app, backend = frontend(project_snapshot(
        phase='SEARCH_PLAN_REVIEW', background_agent={
            'scope_id': 'scope_ui', 'status': 'awaiting_authorization',
            'scope': {'external_information': 'optional',
                      'rationale': '模拟问题可选择阅读背景文献。',
                      'stopping_rule': '覆盖问题中的信息缺口后停止。'},
        }))

    assert button(app, '批准工具范围并继续研究').disabled
    app.checkbox(key=f'basis_ack_{PID}_scope_ui').check().run(timeout=20)
    button(app, '批准工具范围并继续研究').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [
        (PREFIX + '/background-agent/authorize', {
            'scope_id': 'scope_ui', 'allow_network': False,
            'action_budget': 12, 'max_model_calls': 30,
        }),
        (PREFIX + '/continue_async', None),
    ]
    assert app.session_state['research_project']['phase'] == 'BACKGROUND_RESEARCH'
    assert '批准工具范围并继续研究' not in labels(app)


def test_plan_approval_saves_expected_versions_then_continues_without_code_execution(frontend):
    app, backend = frontend(project_snapshot(phase='HUMAN_APPROVAL', approval_status='pending'))

    assert button(app, '批准方案并继续研究').disabled
    app.checkbox(key=f'approval_ack_{PID}_package_ui').check().run(timeout=20)
    button(app, '批准方案并继续研究').click().run(timeout=20)

    assert not app.exception
    assert backend.posts == [
        (PREFIX + '/approve', {'acknowledged': True, 'expected_versions': {'question': 1}}),
        (PREFIX + '/continue_async', None),
    ]
    assert app.session_state['research_project']['phase'] == 'EXECUTION_WAITING'
    assert '批准方案并继续研究' not in labels(app)
    assert '批准并执行计算' not in labels(app)
    assert not button(app, '生成计算方案').disabled


@pytest.mark.parametrize('transport_error', [None, requests.ConnectionError, requests.Timeout])
def test_message_failure_preserves_input_and_retry_uses_same_id(frontend, transport_error):
    app, backend = frontend(project_snapshot(human_dialogue={'pending': {
        'request_id': 'question_queue_retry', 'prompt': '请确认数据来源。',
        'phase': 'QUESTION_FORMULATION',
    }}))
    backend.fail_continue_count = 1
    if transport_error:
        backend.continue_transport_error = transport_error('模拟排队连接失败')
    answer = '仅使用模拟数据，不采用真实数据。'
    next(item for item in app.text_area if item.label == '你的消息').set_value(answer)
    button(app, '发送并继续').click().run(timeout=20)

    assert not app.exception
    assert len(backend.posts) == 1
    assert backend.posts[0][0] == PREFIX + '/message'
    assert backend.posts[0][1]['message'] == answer
    assert app.session_state['research_project']['human_dialogue'].get('pending')
    assert next(item for item in app.text_area if item.label == '你的消息').value == answer
    button(app, '发送并继续').click().run(timeout=20)
    assert not app.exception and len(backend.posts) == 2
    assert backend.posts[0] == backend.posts[1]  # A lost response cannot create another turn.
    assert len(backend.project['human_dialogue']['responses']) == 1
    assert not next(item for item in app.text_area if item.label == '你的消息').value


@pytest.mark.parametrize('error_type', [requests.ConnectionError, requests.Timeout])
def test_unknown_active_job_connection_failure_does_not_offer_a_second_start(frontend, error_type):
    app, backend = frontend()
    job_id = 'job_unknown_connection'
    app.session_state['research_job_id'] = job_id
    backend.get_transport_errors[f'/api/research/jobs/{job_id}'] = error_type('模拟任务状态连接失败')

    app.run(timeout=10)

    assert not app.exception
    assert backend.posts == []
    assert '继续研究' not in labels(app)
    assert '重试当前研究阶段' not in labels(app)
    assert not any((item.key or '').startswith('next_action_') for item in app.button)
    assert any('无法确认运行状态' in item.value for item in app.warning)
    assert not button(app, '刷新项目').disabled


def test_failed_job_refreshes_saved_checkpoint_before_choosing_recovery(frontend):
    stale = project_snapshot(phase='EXECUTION', active_job_id='job_assessment_failed')
    actual = project_snapshot(phase='EXECUTION_WAITING', computation={
        'status': 'needs_input', 'awaiting_assessment': {'run_id': 'already_executed'},
    })
    job = {'job_id': 'job_assessment_failed', 'project_id': PID,
           'phase': 'EXECUTION', 'status': 'failed',
           'error': {'error_type': 'TimeoutError', 'error_message': 'assessment timeout'}}

    app, backend = frontend(stale, job=job, backend_project=actual)

    assert app.session_state['research_project']['phase'] == 'EXECUTION_WAITING'
    assert not button(app, '继续评估已有结果').disabled
    assert '确认并开始项目内执行' not in labels(app)
    assert '生成计算方案' not in labels(app)
    assert backend.posts == []
    button(app, '继续评估已有结果').click().run(timeout=20)
    assert not app.exception
    assert backend.posts == [(PREFIX + '/computation/resume_assessment', {})]


def test_all_frontend_upload_context_literals_are_accepted_by_backend_schema():
    from src.ai_scientist.schemas import ResearchAssetUploadRequest

    tree = ast.parse(APP.read_text(encoding='utf-8'))
    contexts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == 'upload_context' and isinstance(keyword.value, ast.Constant):
                contexts.append(keyword.value.value)
        if (isinstance(node.func, ast.Name) and node.func.id == '_save_research_assets'
                and len(node.args) > 5 and isinstance(node.args[5], ast.Constant)):
            contexts.append(node.args[5].value)
    assert len(contexts) >= 7
    assert 'project_workspace' in contexts
    for context in contexts:
        request = ResearchAssetUploadRequest(filename='context-test.txt',
                                            content_base64='eA==', upload_context=context)
        assert request.upload_context == context
