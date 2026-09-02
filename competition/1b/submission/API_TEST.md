# Competition 1B 公网 API 测试指南

部署后将 `PUBLIC_API_URL` 替换为公网 API 根地址，例如 `https://api.example.com`。本项目不在源码中保存域名、密钥或 token。

- OpenAPI 文档：`PUBLIC_API_URL/docs`
- 健康检查：`GET PUBLIC_API_URL/health`
- Competition 1B 就绪检查：`GET PUBLIC_API_URL/api/competition/1b/readiness`

## 最短代表性案例

先创建项目（不会自动调用模型）：

```bash
curl -X POST "$PUBLIC_API_URL/api/research/start" \
  -H "Content-Type: application/json" \
  -d '{"objective":"分析公开 Iris 数据中萼片长度与花瓣长度的关联","domain_hint":"biology","planning_only":false,"max_iterations":1}'
```

成功响应示例：

```json
{"project_id":"project_demo123","phase":"QUESTION_INTAKE","status":"created"}
```

将返回的 `project_id` 记为 `PROJECT_ID`。每次推进一个阶段：

```bash
curl -X POST "$PUBLIC_API_URL/api/research/$PROJECT_ID/step_async"
curl "$PUBLIC_API_URL/api/research/jobs/$JOB_ID"
curl "$PUBLIC_API_URL/api/research/$PROJECT_ID"
```

`step_async` 返回 `job_id`；轮询任务直到 `status` 为 `completed`，再重复调用下一阶段。项目状态为 `COMPLETED` 后，`GET /api/research/{project_id}` 的 `conclusion`、`evidence`、`claims` 和 `artifacts` 即为结果。

PowerShell：

```powershell
$base = $env:PUBLIC_API_URL
$body = @{ objective = "分析公开 Iris 数据中萼片长度与花瓣长度的关联"; domain_hint = "biology"; planning_only = $false; max_iterations = 1 } | ConvertTo-Json
$created = Invoke-RestMethod -Method Post -Uri "$base/api/research/start" -ContentType 'application/json' -Body $body
$created
$job = Invoke-RestMethod -Method Post -Uri "$base/api/research/$($created.project_id)/step_async"
Invoke-RestMethod "$base/api/research/jobs/$($job.job_id)"
Invoke-RestMethod "$base/api/research/$($created.project_id)"
```

Python (`requests`)：

```python
import os, time, requests
base = os.environ["PUBLIC_API_URL"]
r = requests.post(base + "/api/research/start", json={"objective": "分析公开 Iris 数据中萼片长度与花瓣长度的关联", "planning_only": False, "max_iterations": 1}, timeout=30)
r.raise_for_status(); project_id = r.json()["project_id"]
job = requests.post(f"{base}/api/research/{project_id}/step_async", timeout=30).json()
while True:
    state = requests.get(f"{base}/api/research/jobs/{job['job_id']}", timeout=30).json()
    if state["status"] in {"completed", "failed"}: break
    time.sleep(2)
result = requests.get(f"{base}/api/research/{project_id}", timeout=30).json()
print(result["phase"], result.get("conclusion"))
```

## 查询产物

- `GET /api/research/{project_id}/artifacts`：列出产物、文件名和校验和。
- 研究报告使用 `GET /api/research/{project_id}/report.md` 或 `/report.json` 查看；上传文件使用 `GET /api/research/{project_id}/research-assets/{asset_id}` 下载。产物列表中的 `artifact_id`、文件名和校验和用于审计关联。
- Competition 1B 确定性演示：`POST /api/competition/1b/demo/run`，随后查询 `/demo`、`/demo/history`、`/demo/artifacts`。

## 错误与保护

错误响应采用 JSON `detail`，包含安全的 `error_type`/`error_message`；常见状态码：`400` 参数或状态不合法、`404` 项目/任务不存在、`409` 阶段任务已在运行、`413` 上传超过 20 MiB、`422` Pydantic 请求校验失败、`500` 后端或模型服务错误。服务器环境变量中的密钥不会返回。

每个项目有模型调用和迭代预算（默认最多 50 次模型调用），异步接口拒绝重复运行中的任务。公网部署应在 Nginx/WAF 层增加认证、IP 速率限制和访问日志脱敏；本示例 API 本身不承诺匿名公网防滥用。
