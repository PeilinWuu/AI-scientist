# Qwen Evidence Checklist

| 项目 | 状态 | 证据 |
|---|---|---|
| DashScope OpenAI-compatible Chat Completions 路径 | 已实现 | `src/ai_scientist/structured_client.py`、`src/pure_qwen_client.py` |
| DashScope Responses + web search 路径 | 已实现 | `src/search_qwen_client.py` |
| 实际模型解析 | 已实现 | `src/ai_scientist/model_registry.py`、`.env.example` |
| 结构化上下文与 JSON schema | 已实现 | `StructuredQwenClient.call()` 将 task input 和 output schema 分开传入 |
| 角色分工 | 已实现 | Qwen 负责问题理解、计划、解释、调整建议和科学论证；确定性执行器负责数值 |
| 任意代码隔离 | 已实现 | `ExecutionAdapter` 仅接受白名单 operation；`CodeTool` 明确 rejected |
| 真实 smoke test | **BLOCKED_EXTERNAL_INVALID_CREDENTIAL** | `results/qwen_smoke_test.json`：2026-08-31 实际请求返回 DashScope 401 `invalid_api_key` |

## 更换参赛凭证后的 smoke test

不要提交真实密钥。将有效 `DASHSCOPE_API_KEY` 写入本地 `.env`，然后运行：

```powershell
python -c "from dotenv import load_dotenv; load_dotenv('.env'); from src.pure_qwen_client import PureQwenClient; print(PureQwenClient().chat([{'role':'user','content':'Return exactly: QWEN_SMOKE_OK'}]))"
```

通过后应重新生成脱敏的 `results/qwen_smoke_test.json` 并截图模型、时间和成功状态；不得截图或记录密钥。
