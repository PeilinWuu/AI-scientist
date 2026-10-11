# 自主研究工作台（Beta）

> 历史方案，已退出主入口：2026-10-09 改为固定工作流内调用工具。
> run/execute/reject API 已返回 HTTP 410，历史记录和报告保留。
> 当前使用方式见 [固定流程工具指南](FIXED_WORKFLOW_TOOLS.md)。

这不是“任意问题都能自动得出正确答案”的承诺。此版本在原项目内增加可中断、
可审计的研究控制循环；保留固定阶段工作流、人工审批及已有 Iris/阻尼项目。
工作台独立报告与固定阶段报告分别显示，不能把旧项目状态当成新研究完成的证据。

## 实际行为

每步由 Qwen 依据原问题、真实工具观察、资料台账、既有计算和用户反馈选择工具。
不是按关键词绑定数据集执行器，也不是只有模型互相描述“已经完成”。工具名和参数
经过枚举/Pydantic 校验，只有实际接入的工具可以执行。

| 工具 | 真正执行的动作 | 不表示什么 |
| --- | --- | --- |
| update_plan | 保存和修订问题、假设、验证标准、停止条件与证据缺口 | 计划不是已执行结果，也不允许偷换用户目标 |
| search_web | Qwen Responses 联网搜索，保存查询及返回的引用/请求 ID | 搜索摘要不是已验证证据 |
| search_papers | Crossref 公共 API 查询论文元数据，最多 5 条 | DOI 存在不代表论文结论正确；元数据不是全文 |
| read_source | 直接读取公开 HTTPS 网页/文本/JSON，保存时间与内容哈希 | 读取成功不代表科学真实性已核实 |
| assess_source | 模型记录保留/拒绝、相关性、可信度、局限和支持/反对立场 | 逐字引用校验只证明文字存在；蕴含关系还需复审 |
| inspect_asset | 读取项目内已上传并解析资料的实际内容/数据概况；参考文献文本可进入来源台账并做逐字引用检查 | 不凭文件名猜测数据，也不读取任意宿主路径；上传者提供的作者信息不是独立验证 |
| prepare_computation | Qwen 生成针对当前问题的代码、指标、验证条件和停止规则 | 只生成计划，不会自行执行 |
| ask_user | 因缺数据、范围不清或缺执行能力暂停 | 不以合成数据顶替缺失测量 |
| finish | 校验引用/run ID，再交独立 Qwen 调用复审 | 自动复审通过不等于人类同行评审或科学真理 |

Crossref 参数依据[官方 REST API 文档](https://api.crossref.org/)。
源网页、工具结果和文件始终作为不可信资料；不赋予它们改变权限或发出命令的能力。
检索/来源评估的策略仍可能出错；当前并非 PRISMA 级系统综述检索器。

## 本地运行

使用现有 `.env` 中的 Qwen 配置，不需要额外 API key。联网会消耗模型额度。

```bash
AI_SCIENTIST_ENABLE_COMPUTATION=1 .venv/bin/python -m uvicorn src.main_api:app --host 127.0.0.1 --port 8000
# 另一终端
.venv/bin/python -m streamlit run app_streamlit.py --server.address 127.0.0.1 --server.port 8501
```

部署仍只支持一个 Uvicorn worker；不要直接暴露无鉴权 API 到公网。
启用计算需要 bubblewrap 和科学计算依赖，见 [GENERAL_COMPUTATION.md](GENERAL_COMPUTATION.md)。
启动健康检查现在包含真实 PNG 绘制，避免“只导入库正常，绘图实际失败”。

## 人类前端复现

1. 打开 `http://127.0.0.1:8501`，侧栏后端为 `http://127.0.0.1:8000`。
2. 输入自己的问题并创建项目。若需要计算，在高级设置关闭“仅规划”；有真实数据时上传
   并选择数据用途。文献核查可以保留仅规划模式。
3. 使用“自主研究工作台 · Beta”，不是下方旧的固定阶段推进按钮。
4. 根据任务勾选允许本批次联网，选择行动数（最多 12），点击“开始 / 继续自主研究”。
   默认没有联网权限；未启用计算/仅规划时不向控制器提供计算工具。
5. 打开行动记录检查搜索查询、原文及来源取舍。拒绝来源也保留在台账中。
   可以填写补充信息、纠正范围或要求查找反例，再继续一批。
6. 生成计算计划时自动暂停。核对方法、数据、假设、指标、验证条件和代码，再勾选批准。
   执行只接受该版本的 plan ID；问题/数据/相关配置改变后旧审批失效。
   也可以拒绝并填写原因。失败后的代码修改必须重新生成和审批，不能悄悄换算法。
7. 执行完点击继续研究，让控制器读取真实结果、补充检索或提出下一轮计算。
   每个工作台最多 6 次计算，不允许无上限试到显著。
8. 报告必须引用已经读取并评估的来源或成功计算的 run ID。独立复审要求修改时，
   控制器可继续调用工具；需要数据或三次复审未通过时暂停请人处理。
9. 下载自主研究报告和完整 JSON 台账；计算产物中可以预览 PNG、下载 CSV/result.json。
10. “当前行动结束后暂停”是协作式暂停，不会强杀正在进行的请求或破坏写入。

建议验收三类新问题，而不是重复 Iris/阻尼：方法学文献比较、上传表格的统计检验、
明确指定的数值模拟。另加一个故意缺数据的问题，确认系统没有编造结果。

## 预算、安全和复现

- 每批 1–12 步，360 秒后不再开始下一步；单个模型请求最多 180 秒，联网搜索 90 秒。
  网络连接/DNS 等外部故障仍可能影响实际墙钟时间。每个工作台最多 64 步、20 个原文快照。
- 控制调用禁用隐式重试/模型替换，按项目 attempted_model_calls 预扣调用额度。
  实际模型回执/请求 ID 和 token 使用保存在版本化 artifacts 中。
  输出违反 schema 时最多显式重试一次，并保存失败和再次调用记录、再次扣减预算；
  此操作不执行代码，也不更改已经批准的方案。
  每次最多 10000 个输出 token；Qwen3.x 使用显式 thinking_budget，默认 2048，可通过
  `AI_SCIENTIST_WORKSPACE_THINKING_BUDGET` 调整（256–8192）。思考预算和超时不是
  科研质量保证；较复杂课题需要人工评估是否增加预算。参数规则见
  [百炼官方说明](https://www.alibabacloud.com/help/zh/model-studio/deep-thinking)。
- 生成代码每次必须人工批准。输入哈希、代码哈希、随机种子、软件版本、stdout/stderr、
  结果哈希和指标可查。进程意外消失后不能自动重跑已消费的审批。
- 沙盒无网络、无项目密钥、输入只读；输出最多 64 项，临时缓存最多 256 项，共计 32 MiB。
  字体配置和公共字体缓存只读挂载，不挂载整个 `/etc`。科学库/绘图均有实机测试。
- 原文下载只允许公开 HTTPS/443；拒绝私网、回环、混合 DNS；校验后固定连接 IP，
  TLS 按原主机名验证，每次重定向重新检查；不使用宿主 cookie、代理或凭据。
- 不下载执行网页代码。网页最多 2 MiB，文本最多 24000 字符；PDF 可人工上传本地解析，
  需要登录、JS 渲染和付费墙的文章会明确失败，不伪装成读过全文。
- 检索只是公开资料读取。没有部署任意终端、安装依赖、实验室控制或外部写入能力。

## API 与测试

```text
POST /api/research/{project_id}/workspace/run
  {"max_steps": 6, "allow_network": true, "feedback": "补充指示"}
POST /api/research/{project_id}/workspace/execute
  {"plan_id": "人工已审核的准确 ID"}
POST /api/research/{project_id}/workspace/reject
  {"plan_id": "待审批 ID", "reason": "拒绝原因"}
POST /api/research/{project_id}/workspace/pause
GET  /api/research/jobs/{job_id}
GET  /api/research/{project_id}  # research_workspace 字段
GET  /api/research/{project_id}/workspace/report.md
```

run/execute 返回 202 和后台 job ID；项目已有任务时拒绝其他修改（409），暂停请求例外。
重启后遗留 job 标记失败；最后已保存的行动仍可检查。
任务恢复只在 API 服务启动时执行，不在导入模块时执行。pytest 使用临时项目根目录，
不会把正在运行的开发服务的任务误判为遗留任务。

```bash
RUN_NAMESPACE_TESTS=1 .venv/bin/python -m pytest -q
RUN_NAMESPACE_TESTS=1 .venv/bin/python -m pytest tests/ai_scientist/test_research_workspace.py -q
```

单元测试替身不算真实 Qwen 验收；测试通过也不意味着任意科研问题都可解决。
必须另行检查真实检索内容、统计有效性、数值误差和未见问题上的表现。
