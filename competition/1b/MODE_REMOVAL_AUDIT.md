# Standalone Qwen Mode Removal Audit

审计基线：`f5779ef`，分支 `competition/1b-final-sprint`，工作区 clean。

## Pure Qwen 独立模式

涉及文件：

- `app_streamlit.py`：`APP_MODES`、`render_chat_mode()`、聊天 session state 和 Pure Qwen UI 分支。
- `src/main_api.py`：`/api/chat`、`/api/debug_payload`、`/api/qwen_ping` 及专用响应处理。
- `src/pure_schemas.py`：只服务独立聊天 API 的请求、响应和可见历史 schema。
- `tests/test_pure_qwen_payload.py`：既包含独立模式 API/UI 测试，也包含底层 client 安全测试。
- `README.md`、`update.md`：旧产品模式说明。

安全删除：独立 Streamlit 聊天页、专用 session state、三个专用 API endpoint、专用 schemas，以及只验证这些入口的测试。

必须保留：`src/pure_qwen_client.py`。Competition readiness 的 authenticated smoke test 直接使用
`PureQwenClient`；`/api/models/test` 也使用它验证 AI Scientist 非检索角色模型。底层 client 的无代理、
不携带搜索参数和密钥延迟加载测试应保留为共享基础设施测试。

## Qwen Search 独立模式

涉及文件：

- `app_streamlit.py`：独立搜索聊天页、response continuity state、source/debug 展示。
- `src/main_api.py`：`/api/chat_search`、`/api/debug_search_payload`、`/api/search_ping`。
- `src/search_schemas.py`：只服务独立 Search chat API。
- `tests/test_pure_qwen_payload.py`：独立 route/UI 测试与底层 Responses/search extraction 测试混合。

安全删除：独立 Search UI、专用 session state、三个专用 API endpoint、专用 schemas，以及只验证这些入口的测试。

必须保留：

- `src/search_qwen_client.py`：AI Scientist `tools/search_tools.py` 和 Evidence Researcher 的实际联网底座。
- `resolve_search_tools`、Responses output 提取、source/tool usage 解析逻辑及其底层测试。
- `src/ai_scientist/` 内的 SearchPlan、bounded acquisition、Source Selection、Extraction、Normalization、
  Claim–Evidence mapping、checkpoint、预算与审计。
- `/api/models/test` 的 `search` 分支和 AI Scientist evidence debug/search ping；它们是内部角色诊断，不是独立产品模式。

## 删除后的入口

- Streamlit 只有一个 `AI Scientist` 产品入口，首屏从用户科学问题和可选附件开始。
- Competition 1B 阻尼振子只作为可编辑的“加载示例”存在；加载不会创建项目或执行。用户点击
  Start Research 后，显式 example metadata 将项目绑定到现有白名单确定性 executor；Round 1/2、
  FeedbackSignal、PlanAdjustment 和 comparison 均保存在同一 AI Scientist 项目中。
- OpenAPI 只保留 Competition 1B、AI Scientist、内部模型诊断和 health/readiness；不再暴露独立聊天 endpoints。
- Qwen 推理和联网检索仍作为 AI Scientist 内部能力存在，不作为独立产品呈现。
