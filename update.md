# FlowScientist 项目进度更新

更新时间：2026-07-11

## 1. 当前项目定位

当前项目已经从早期的固定实验循环原型，重构为一个以 Qwen 为核心的研究交互系统。现阶段不是单一 simulator，也不是单纯的 prompt 包装，而是保留三条相互隔离的主链路：

1. **Pure Qwen Shell**
   - 纯 Qwen 聊天模式。
   - 不注入 system prompt。
   - 不加载 skill。
   - 不调用 tools。
   - 不做 RAG。
   - 不做实验规划。
   - 用于验证 Qwen API 的最小、干净主链路。

2. **Qwen Search**
   - 使用 Qwen Responses API 的原生联网工具。
   - 使用 `web_search` 和 `web_extractor`。
   - 不手动拼接搜索上下文。
   - 不把搜索中间结果、系统提示、原始检索片段写入聊天历史。
   - 多轮联网上下文通过 `previous_response_id` 维护。

3. **AI Scientist**
   - 新增的独立研究规划模式。
   - 领域无关，不再绑定软体游动机器人。
   - 通过多个 Qwen 角色协作完成研究问题形式化、证据检索、方法选择、假设生成、研究设计、分析计划、可复现性检查、独立审查和最终综合。
   - 当前定位是“研究规划与证据约束的 AI Scientist”，不是自动生成虚假实验结果。

## 2. 后端当前状态

后端仍然使用 FastAPI。

核心文件：

- `src/main_api.py`
- `src/pure_qwen_client.py`
- `src/search_qwen_client.py`
- `src/ai_scientist/`

已保留的 Pure Qwen 接口：

- `GET /health`
- `GET /api/qwen_ping`
- `POST /api/debug_payload`
- `POST /api/chat`

已保留的联网搜索接口：

- `GET /api/search_ping`
- `POST /api/debug_search_payload`
- `POST /api/chat_search`

新增的 AI Scientist 接口：

- `POST /api/research/start`
- `GET /api/research/{project_id}`
- `POST /api/research/{project_id}/step`
- `POST /api/research/{project_id}/step_async`
- `GET /api/research/jobs/{job_id}`
- `POST /api/research/{project_id}/approve`
- `POST /api/research/{project_id}/revise`
- `POST /api/research/{project_id}/provide-data`
- `POST /api/research/{project_id}/cancel`
- `GET /api/research/{project_id}/claims`
- `GET /api/research/{project_id}/evidence`
- `GET /api/research/{project_id}/hypotheses`
- `GET /api/research/{project_id}/artifacts`
- `GET /api/research/{project_id}/events`
- `GET /api/research/{project_id}/capabilities`

## 3. AI Scientist 模块实现进度

AI Scientist 独立放在：

```text
src/ai_scientist/
```

当前已实现：

- 研究项目 schema
- 研究阶段状态机
- 多角色 agent 框架
- Qwen 结构化 JSON 调用客户端
- Qwen 模型角色注册与 fallback 记录
- skill YAML 加载器
- 方法选择器
- 领域路由器
- claim-evidence graph
- artifact store
- project store
- event log
- 异步 job store
- 工具能力注册表
- placeholder execution adapter

当前 AI Scientist 角色包括：

- Research Director
- Evidence Researcher
- Methodologist
- Hypothesis Scientist
- Study Designer
- Analyst
- Reproducibility Engineer
- Skeptical Reviewer
- Scientific Synthesizer

当前 AI Scientist 阶段包括：

- `INTAKE`
- `QUESTION_FORMULATION`
- `RESEARCH_MODE_SELECTION`
- `DOMAIN_SELECTION`
- `BACKGROUND_RESEARCH`
- `CLAIM_EVIDENCE_MAPPING`
- `HYPOTHESIS_GENERATION`
- `METHOD_SELECTION`
- `STUDY_DESIGN`
- `ANALYSIS_PLANNING`
- `FEASIBILITY_REVIEW`
- `HUMAN_APPROVAL`
- `EXECUTION_WAITING`
- `DATA_ANALYSIS`
- `CRITICAL_REVIEW`
- `REVISION`
- `SYNTHESIS`
- `COMPLETED`
- `FAILED`
- `CANCELLED`

## 4. 最近修复的关键问题

### 4.1 Streamlit `_repr_html_()` 报错

之前 AI Scientist 前端中存在类似写法：

```python
st.dataframe(claims) if claims else st.info(...)
```

这会导致 Streamlit 出现：

```text
StreamlitAPIException: _repr_html_() is not a valid Streamlit command.
```

现在已经修复：

- 删除所有 Streamlit 命令三元表达式。
- 改为显式 `if / else`。
- 新增 `normalize_records()`，确保表格数据渲染前转换为普通 dict list。
- `evidence / claims / artifacts / conclusion` 的空数据和非空数据都能安全显示。

### 4.2 BACKGROUND_RESEARCH 长请求超时

之前前端点击“运行下一阶段”时，直接调用：

```text
POST /api/research/{project_id}/step
```

如果阶段中包含真实联网搜索或多次 Qwen 调用，容易超过 180 秒并触发前端 ReadTimeout。

现在已经修复：

- 保留同步接口 `/step`，不破坏旧 API。
- 新增异步接口 `/step_async`。
- 新增 job 查询接口 `/api/research/jobs/{job_id}`。
- 前端默认使用异步 job。
- 任务状态持久化为 JSON 文件。
- 前端轮询 job 状态，而不是等待单个长 HTTP 请求。

job 文件保存位置：

```text
data/research_projects/{project_id}/jobs/{job_id}.json
```

job 状态包括：

```text
queued -> running -> completed
queued -> running -> failed
```

### 4.3 防止重复执行同一阶段

现在如果某个 project 已经存在 queued 或 running 的阶段 job，再次调用 `step_async` 会返回：

```json
{
  "detail": {
    "error": "project_step_already_running",
    "job_id": "...",
    "project_id": "..."
  }
}
```

前端会识别这个状态，并继续轮询已有 job，不会重复启动同一阶段。

### 4.4 阶段失败不再直接污染整个项目

之前某个阶段失败时，项目可能被直接置为 `FAILED`。

现在改为：

- 项目停留在最后一个完整阶段。
- 写入 failed event。
- 前端显示简洁错误。
- Developer debug 开启时显示 job 详情。

这样避免一次联网超时或模型结构化失败导致整个研究项目报废。

## 5. 前端当前状态

前端仍然使用 Streamlit：

```text
app_streamlit.py
```

侧边栏可选择三种模式：

- Pure Qwen
- Qwen Search
- AI Scientist

Pure Qwen：

- 普通聊天。
- 只显示用户消息和模型回答。

Qwen Search：

- 联网搜索聊天。
- 普通用户界面只显示最终回答。
- Developer debug 开启时才显示 response id、request id、sources、tool usage、debug payload。

AI Scientist：

- 可以创建或加载研究项目。
- 显示当前 phase、mode、domain、iteration、model call budget。
- 可以运行下一阶段。
- 可以批准、要求修改、提供数据、取消项目。
- 可以查看 question、evidence、claims、hypotheses、method/design、reviewer、synthesis、events/artifacts。
- 阶段执行默认使用异步 job。
- running 状态下会禁用重复提交。

## 6. 数据持久化状态

AI Scientist 项目保存到：

```text
data/research_projects/
```

每个 project 目录中包含：

```text
project.json
events.jsonl
artifacts/
jobs/
```

其中：

- `project.json` 保存项目当前结构化状态。
- `events.jsonl` 保存阶段事件。
- `artifacts/` 保存结构化产物。
- `jobs/` 保存异步阶段执行任务状态。

当前明确不保存：

- API Key
- Authorization Header
- 完整隐藏 prompt
- 模型隐藏推理
- 原始搜索上下文

## 7. 配置项进度

`.env.example` 已包含基础 Qwen 配置：

```env
DASHSCOPE_API_KEY=
LLM_MODEL=qwen-turbo
LLM_SEARCH_MODEL=qwen3.7-plus
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
RESPONSES_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_TIMEOUT=120
```

AI Scientist 角色模型配置：

```env
AI_SCIENTIST_DIRECTOR_MODEL=
AI_SCIENTIST_RESEARCH_MODEL=
AI_SCIENTIST_METHODOLOGIST_MODEL=
AI_SCIENTIST_HYPOTHESIS_MODEL=
AI_SCIENTIST_DESIGNER_MODEL=
AI_SCIENTIST_ANALYST_MODEL=
AI_SCIENTIST_REPRODUCIBILITY_MODEL=
AI_SCIENTIST_REVIEWER_MODEL=
AI_SCIENTIST_SYNTHESIZER_MODEL=
AI_SCIENTIST_FALLBACK_MODEL=
```

AI Scientist 预算和 timeout 配置：

```env
AI_SCIENTIST_MAX_MODEL_CALLS=50
AI_SCIENTIST_MAX_ITERATIONS=2
AI_SCIENTIST_PROJECTS_DIR=data/research_projects
AI_SCIENTIST_STRUCTURED_RETRY=1
AI_SCIENTIST_DEFAULT_PLANNING_ONLY=true
AI_SCIENTIST_FRONTEND_STEP_TIMEOUT=600
AI_SCIENTIST_MODEL_TIMEOUT=300
AI_SCIENTIST_SEARCH_TIMEOUT=300
```

## 8. 当前能力边界

当前 AI Scientist 可以做：

- 研究问题形式化。
- 研究模式选择。
- 领域路由。
- 联网背景证据检索。
- claim-evidence 映射。
- 假设生成。
- 方法学设计。
- 研究设计。
- 分析计划。
- 可复现性检查。
- 独立 reviewer 审查。
- 研究规划综合。

当前 AI Scientist 不能做：

- 真实实验执行。
- 真实仿真执行。
- 自动运行 CFD、FreeFlow 或实验室设备。
- 伪造实验数据。
- 在没有数据的情况下生成实验结论。

当前 execution adapter 是 placeholder：

```text
No execution backend is currently connected.
```

这是一种有意设计：项目可以规划研究任务，但不会假装已经完成真实实验。

## 9. 已验证情况

最近一次验证通过：

```bash
pytest -q
```

结果：

```text
37 passed, 1 warning
```

编译验证通过：

```bash
python -m compileall -q app_streamlit.py src tests
```

Streamlit AppTest 通过：

```text
default_exceptions = 0
ai_exceptions = 0
titles = ['AI Scientist']
```

源码扫描确认：

- 没有剩余 Streamlit 命令三元表达式。
- 没有 `_repr_html_` 相关错误命中。

## 10. 如何运行

启动后端：

```bash
python -m uvicorn src.main_api:app --reload --host 127.0.0.1 --port 8000
```

启动前端：

```bash
streamlit run app_streamlit.py
```

浏览器打开 Streamlit 页面后，可在侧边栏选择：

- Pure Qwen
- Qwen Search
- AI Scientist

## 11. 当前最适合展示的功能

如果用于阶段性展示，建议展示：

1. Pure Qwen 模式：证明 Qwen 基础 API 链路干净可用。
2. Qwen Search 模式：证明可以使用 Qwen 原生联网搜索，并且不会泄漏搜索 prompt 或原始检索上下文。
3. AI Scientist 模式：证明系统已经从聊天壳升级为结构化研究工作流。
4. AI Scientist 事件日志：展示每个阶段由哪个 agent 产生、使用哪个模型、是否 fallback、是否调用搜索工具。
5. 异步 job 机制：展示 BACKGROUND_RESEARCH 可以长时间执行，前端不会 timeout 崩溃。

## 12. 下一步建议

下一步可以继续做四件事：

1. **真实跑通一条完整 AI Scientist project**
   - 从 INTAKE 一直推进到 HUMAN_APPROVAL 或 SYNTHESIS。
   - 记录每个阶段耗时、Qwen 调用次数和搜索工具使用情况。

2. **优化 AI Scientist 前端可读性**
   - 当前功能完整，但 UI 仍偏工程调试。
   - 可以增加 timeline、阶段卡片、agent 状态面板和报告导出。

3. **接入真实执行 backend**
   - 当前 execution adapter 是 placeholder。
   - 后续可以接入 FreeFlow、CFD、Python 仿真脚本、实验设备或数据分析 notebook。

4. **增加比赛演示脚本**
   - 准备一个标准研究目标。
   - 固定展示路径。
   - 展示“规划、搜索、审查、等待真实执行”的可信闭环，而不是伪造结果。

## 13. 总结

当前项目已经完成从“普通 Qwen 聊天壳”到“三模式 Qwen Research Shell”的关键过渡：

- Pure Qwen 保持干净。
- Qwen Search 支持原生联网。
- AI Scientist 已具备结构化、多角色、可审计、异步执行的研究规划能力。

最重要的架构原则也已经落地：

- 不伪造实验。
- 不把搜索中间上下文暴露给用户。
- 不把旧 agent 逻辑污染 Pure Qwen。
- 不让前端长请求超时破坏研究流程。
- 所有 AI Scientist 阶段产物和事件都可追踪。

## 14. V0.2 科研规划质量验收版更新

本轮已把 AI Scientist 升级为 V0.2“科研规划质量验收版”的最小可运行实现。重点不是新增 Agent，也不是接入真实实验工具，而是让 planning-only 研究方案具备质量指标、人工编辑、定向回退、版本管理和正式报告。

新增能力：

- 新增 `ResearchQualityMetrics`，包括 evidence coverage、hypothesis completeness、conclusion traceability、reviewer min score、unverifiable source count 等。
- 扩展 `EvidenceItem`，增加 source level、primary source、verified、duplicate_of、retrieval_date、reliability_score、relevance_score。
- 新增证据分级和去重逻辑，支持 DOI、URL、标题/年份级别的轻量去重。
- 新增 `ConclusionItem`，supported findings 现在可以显式绑定 supporting claim IDs。
- `ClaimGraph` 增加新版 conclusion traceability 校验。
- Reviewer 结果增加 failed quality gates 和 required revision target。
- Reviewer quality gates 会强制阻止低质量方案 approve。
- planning-only 项目在人类批准后直接进入 SYNTHESIS，不再进入真实 EXECUTION。
- SYNTHESIS 阶段生成正式报告：
  - `artifacts/research_plan.md`
  - `artifacts/research_plan.json`
- 报告中明确声明尚未执行真实实验、仿真或数据分析，不能视为实验结论。
- 关键产物开始按版本保存，例如 `question_v1.json`、`evidence_map_v1.json`、`study_design_v1.json`。
- 新增人工编辑 API：
  - `PATCH /api/research/{project_id}/question`
  - `PATCH /api/research/{project_id}/hypotheses/{hypothesis_id}`
  - `PATCH /api/research/{project_id}/study-design`
  - `PATCH /api/research/{project_id}/analysis-plan`
  - `POST /api/research/{project_id}/evidence`
  - `DELETE /api/research/{project_id}/evidence/{evidence_id}`
- 人工编辑会写入 `human_edit` event，并触发定向回退。
- 新增报告读取接口：
  - `GET /api/research/{project_id}/report.md`
  - `GET /api/research/{project_id}/report.json`
- Streamlit AI Scientist 页面新增 Research Quality 面板。
- Streamlit 支持下载 `research_plan.md` 和 `research_plan.json`。
- Streamlit 增加结构化人工编辑面板。
- 新增 benchmark 配置：
  - `data/benchmarks/remote_work_productivity.json`
  - `data/benchmarks/image_classification_algorithm.json`
  - `data/benchmarks/microplastics_systematic_review.json`
- 新增 benchmark runner：
  - `tools/run_benchmarks.py`

本轮验证：

```bash
pytest -q
```

结果：

```text
40 passed, 1 warning
```

```bash
python -m compileall -q app_streamlit.py src tests tools
```

结果：通过。

Streamlit AppTest：

```text
default_exceptions = 0
ai_exceptions = 0
titles = ['AI Scientist']
```

Benchmark 初始化脚本已验证：

```bash
python tools/run_benchmarks.py
```

已生成三个 benchmark result JSON：

- `data/benchmarks/image_classification_algorithm_benchmark_result.json`
- `data/benchmarks/microplastics_systematic_review_benchmark_result.json`
- `data/benchmarks/remote_work_productivity_benchmark_result.json`

当前仍需注意：

- benchmark 默认只初始化项目和结果结构，不自动消耗大量 Qwen 调用跑完整流程。
- 如需真实完整 benchmark，可使用 `python tools/run_benchmarks.py --run-to-completion`，但这会触发多轮真实 Qwen 调用和联网搜索。
- 当前 execution adapter 仍是 placeholder，不会伪造实验或仿真结果。

## 15. AI Scientist 主链路重构更新

本轮针对 AI Scientist 做了主链路重构，重点解决两个问题：

1. 多 Agent 阶段中存在“本地规则完成但被显示为 Agent completed”的情况。
2. 前端向普通用户暴露过多 JSON、dict、内部产物和枚举字段。

已完成改动：

- `RESEARCH_MODE_SELECTION` 现在由 `MethodologistAgent` 真实调用 Qwen，不再由本地 `MethodSelector` 作为主路径完成。
- `DOMAIN_SELECTION` 现在通过 `StructuredQwenClient` 使用 `research_director` 角色模型进行结构化领域判断，本地 `DomainRouter` 只保留为辅助能力。
- `CLAIM_EVIDENCE_MAPPING` 现在由 `evidence_researcher` 角色模型整理主张与证据映射，再由本地 `ClaimGraph` 做确定性校验。
- 修复 `BACKGROUND_RESEARCH` 立即失败的关键参数错误：`previous_response_id` 现在通过 keyword 传给 Qwen Search，不会误传为 model。
- `BACKGROUND_RESEARCH` 已明确拆成两步：
  - Qwen Search 获取自然语言搜索结果和来源。
  - `AI_SCIENTIST_RESEARCH_MODEL` 再做结构化证据整理。
- 模型调用计数扩展为：
  - `attempted_model_calls`
  - `successful_model_calls`
  - `failed_model_calls`
  - `fallback_model_calls`
- Agent 成功事件会记录自然语言 `display_markdown`，用于前端研究日志。
- 失败事件会记录更具体的安全诊断字段：
  - `failing_component`
  - `stage_substep`
  - `tool_name`
  - `safe_traceback`
- 新增 `src/ai_scientist/presentation.py`，负责把内部结构转换为中文 Markdown。
- Streamlit AI Scientist 创建项目时，不再要求填写 `Constraints JSON`，改为自然语言“补充要求与约束”。
- Streamlit AI Scientist 普通界面不再直接展示：
  - `st.json`
  - `project.json`
  - `model_dump_json`
  - raw response
  - internal_data
  - JSON schema
- AI Scientist 默认下载只提供 `research_plan.md`，内部 `research_plan.json` API 仍保留给审计和恢复。
- 前端新增自然语言研究日志区，显示各阶段 agent 的中文反馈。

新增测试覆盖：

- `RESEARCH_MODE_SELECTION` 会进入 Methodologist 模型阶段。
- `DOMAIN_SELECTION` 会进入结构化 Qwen 模型阶段。
- `CLAIM_EVIDENCE_MAPPING` 会进入 Evidence Researcher 模型阶段。
- `QwenEvidenceSearchTool` 正确用 keyword 传递 `previous_response_id`。
- 普通前端不再包含 `st.json`、`Constraints JSON`、`project.json` 等泄漏点。

最新验证：

```bash
pytest -q
```

结果：

```text
44 passed, 1 warning
```

```bash
python -m compileall -q app_streamlit.py src tests tools
```

结果：通过。

Streamlit AppTest：

```text
default_exceptions = 0
ai_exceptions = 0
titles = ['AI Scientist']
```
