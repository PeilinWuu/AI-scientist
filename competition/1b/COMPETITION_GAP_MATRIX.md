# Competition 1B Gap Matrix

审计日期：2026-08-31
审计分支：`competition/1b-final-sprint`
基线：`161 passed, 1 warning`

| 比赛交付 | 状态 | 当前证据 | 计划改动 | 主要风险 | 优先级 |
|---|---|---|---|---|---|
| 科研问题到结构化研究计划 | 已完成 | `src/ai_scientist/orchestrator.py`、`schemas.py`、`general_research_v1.yaml` | 保持现有链路，不重构 | 真实模型调用依赖外部凭证 | P0 |
| 证据检索、人审与可追溯映射 | 已完成 | `evidence_curation.py`、`claim_graph.py`、相关测试 | 在提交材料中映射证据 | 网络与搜索模型可用性 | P1 |
| 独立审稿和人工修订 | 已完成 | `revision_workflow.py`、`test_independent_review_revision_workflow.py` | 复用其审计原则 | 不直接用于数值迭代 | P1 |
| 上传文件解析并进入 Agent 上下文 | 已完成 | `document_parsers.py`、`test_document_parsers.py` | 修正 README 旧描述 | 扫描 PDF 暂无 OCR | P1 |
| 安全白名单执行器 | 缺失 | `tools/execution_adapter.py` 仅抛出 `NotImplementedError` | 实现结构化请求、校验、fingerprint、结果及 artifact | 数值依赖与路径边界 | P0 |
| 确定性统计/仿真分析 | 缺失 | 四个 tool 文件为空占位 | 实现数据检查、缺失率、描述统计、相关性、线性回归、图表和阻尼振子仿真 | 避免引入代码执行 | P0 |
| 机器可审计反馈对象 | 缺失 | 现有 revision 对象面向文稿修订 | 新增 FeedbackSignal、PlanAdjustment、IterationRecord | old/new 与证据引用必须完整 | P0 |
| Round 1 结果驱动 Round 2 | 缺失 | workflow 在执行边界等待 | 新增独立 competition runtime，显式消费上一轮结果 | 不能退化为重新生成 | P0 |
| 旗舰可复现实例 | 缺失 | 无两轮执行案例 | 阻尼振子参数辨识，多 seed 运行 | 指标需稳定改善且保留代价 | P0 |
| 无反馈 baseline | 缺失 | 无 benchmark | 相同预算下重复初始粗网格，与反馈收窄网格比较 | 公平性定义需清楚 | P0 |
| 失败与人工接管证据 | 部分完成 | 通用 API 有结构化错误，执行层没有 | 覆盖缺失文件、非法参数、非法 operation、约束冲突 | 失败不能静默 | P0 |
| FastAPI 比赛入口 | 部分完成 | 现有研究 API 完整，无执行迭代 API | 增加 competition demo 创建、执行、历史、artifact/readiness 路由 | 避免路由重复 | P0 |
| Streamlit 最短演示路径 | 部分完成 | `app_streamlit.py` 有三模式工作台 | 在 AI Scientist 内增加 Competition Demo 区块 | 不能破坏原 UI | P0 |
| 原始数据、分析、diff、审计落盘 | 部分完成 | 已有项目 store、artifact、events | competition 案例使用独立稳定目录并保存 checksum/provenance | 命名覆盖旧结果 | P0 |
| Qwen 参赛证据 | 部分完成 | `structured_client.py`、Qwen client 与模型注册表 | 生成清单并运行凭证状态检查；无有效调用不得声称通过 | 外部凭证/网络 | P1 |
| 一键复现和 readiness | 缺失 | 仅有 pytest/启动命令 | 新增 CLI runner 和 readiness JSON | 必须检查实际产物而非代码 | P0 |
| PPT、演示、证据索引 | 缺失 | 无 competition submission 目录 | 仅以真实运行数据生成材料骨架 | 截图需人工完成 | P1 |
| README/config 一致性 | 部分完成 | README 前后冲突，timeout 重复 | 低风险修正 | 无 | P1 |
| CI/lint/type/coverage | 缺失 | 无 CI 或 lint 配置 | P0 完成后仅加轻量、局部门禁 | 全仓格式化风险 | P2 |

## 主动延期

- 不拆分 `orchestrator.py`、`schemas.py` 或 `main_api.py`。
- 不提供任意 Python/code 执行沙箱。
- 不连接实验室硬件、CFD 或外部调度系统。
- 不扩大学科 Skill 数量。
- 不为全仓零类型错误制造大规模改动。
