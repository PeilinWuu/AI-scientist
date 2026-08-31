# Qwen Evidence Checklist

| 项目 | 状态 | 证据 |
|---|---|---|
| DashScope OpenAI-compatible Chat Completions 路径 | 已实现 | `src/ai_scientist/structured_client.py`、`src/pure_qwen_client.py` |
| DashScope Responses + web search 路径 | 已实现 | `src/search_qwen_client.py` |
| 实际模型解析 | 已实现 | `src/ai_scientist/model_registry.py`、`.env.example` |
| 结构化上下文与 JSON schema | 已实现 | `StructuredQwenClient.call()` 将 task input 和 output schema 分开传入 |
| 角色分工 | 已实现 | Qwen 负责问题理解、计划、解释、调整建议和科学论证；确定性执行器负责数值 |
| 任意代码隔离 | 已实现 | `ExecutionAdapter` 仅接受白名单 operation；`CodeTool` 明确 rejected |
| 真实 authenticated smoke test | **PASSED** | `results/qwen_smoke_evidence.json`：模型 `qwen3.8-max`，精确响应 `QWEN_SMOKE_OK` |

## 可审计 smoke test

不要提交真实密钥。将有效 `DASHSCOPE_API_KEY`、`LLM_MODEL` 和 `LLM_BASE_URL` 写入本地
`.env`，然后显式运行一次真实调用：

```powershell
python -m src.ai_scientist.competition_readiness --run-qwen-smoke
```

命令使用真实 `PureQwenClient`，只在响应精确等于 `QWEN_SMOKE_OK` 时通过，并生成脱敏的
`results/qwen_smoke_evidence.json`。普通 readiness 读取最近一次有效证据，不重复产生 API 费用。
默认证据有效期为 168 小时，可通过 `AI_SCIENTIST_QWEN_SMOKE_MAX_AGE_HOURS` 配置。

2026-08-31 已完成真实 Alibaba Cloud Bailian / Qwen authenticated smoke test：模型
`qwen3.8-max`，响应标记 `QWEN_SMOKE_OK`，状态 `PASSED`。证据不包含 API Key、Authorization
header 或 `.env` 内容。
