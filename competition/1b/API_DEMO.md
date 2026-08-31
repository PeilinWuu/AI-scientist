# Competition 1B API Demo

启动：

```powershell
python -m uvicorn src.main_api:app --host 127.0.0.1 --port 8000
```

就绪检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/competition/1b/readiness
```

运行真实两轮闭环与 baseline：

```powershell
$body = @{ seed = 20260831 } | ConvertTo-Json
Invoke-RestMethod -Method Post -ContentType application/json -Body $body `
  http://127.0.0.1:8000/api/competition/1b/demo/run
```

读取状态、迭代事件和 artifacts：

```text
GET /api/competition/1b/demo
GET /api/competition/1b/demo/history
GET /api/competition/1b/demo/artifacts
GET /api/competition/1b/demo/artifacts/{artifact_path}
POST /api/competition/1b/demo/failure-cases
```

成功响应包含 `plans`、`executions`、`iterations`、`baseline_execution` 和程序计算的
`comparison`。负 seed 或多余字段返回 FastAPI 422；执行失败返回结构化 500；未运行时读取
demo 返回 404。OpenAPI 页面位于 `/docs`。
