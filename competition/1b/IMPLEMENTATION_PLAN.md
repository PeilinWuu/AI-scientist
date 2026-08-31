# Competition 1B Five-Day Implementation Plan

目标是在五天边界内交付一条真实、可重复、可审计的两轮实验反馈闭环。所有数值由确定性程序计算，LLM 结果与执行结果分离。

## Day 1 — P0 执行与数据结构

- 新增 `competition_schemas.py`：执行请求/结果、反馈、调整和迭代记录。
- 将 `tools/execution_adapter.py` 实现为白名单执行器，不接受代码字符串。
- 新增 `competition_runtime.py`，负责目录边界、artifact、事件日志和两轮状态。
- 实现数据检查、缺失率、描述统计、相关性、线性回归、直方图、散点图及阻尼振子仿真/参数拟合。
- 验收：`python -m pytest -q tests/ai_scientist/test_competition_execution.py`。

## Day 2 — P0 旗舰闭环与 baseline

- 创建固定 seed 的阻尼振子观测输入。
- Round 1 使用粗参数网格拟合；反馈根据边界命中和 RMSE 生成收窄/加密调整；Round 2 实际重跑。
- Baseline 在同一第二轮预算下重复无反馈计划。
- 使用 5 个 seed 生成真实汇总、图表、plan diff、provenance 和 audit。
- 验收：`python -m src.ai_scientist.competition_cli run-flagship --output competition/1b`。

## Day 3 — P0 API、UI、失败路径

- 在 `main_api.py` 暴露 competition demo 创建/运行/状态/history/artifacts/readiness。
- 在 `app_streamlit.py` 增加 Competition Demo 最短路径，不重做现有 UI。
- 验证缺失文件、非法 operation、参数越界、约束冲突至少三类失败。
- 验收：competition API happy/failure tests；Streamlit import/start smoke。

## Day 4 — P1 材料与一致性

- 修正 README 上传解析描述和 `.env.example` 重复项。
- 对四个占位 tool 作出明确实现/弃用决定。
- 生成 API demo、Qwen evidence、PPT 内容、演示脚本、复现说明和 evidence index。
- 所有指标从案例 JSON 读取，不手填虚假结果。

## Day 5 — 验收与提交准备

- 实现 readiness checker，区分 `PASSED`、`MISSING` 与 `BLOCKED_EXTERNAL`。
- 运行全量 pytest、旗舰两轮、baseline、失败案例、API/UI smoke。
- 保存最终准备度 JSON，核对 Git diff 和密钥泄漏风险。
- 验收：`python -m src.ai_scientist.competition_readiness` 与 `python -m pytest -q`。

## 设计约束

- 新增 competition service 与现有 AI Scientist 共享 API/UI，但不在冲刺期重写主状态机。
- 只允许枚举 operation；禁止 `exec`、`eval`、shell 或 LLM 生成代码执行。
- 输入文件必须解析为项目目录内的已解析路径。
- 每轮保存不可覆盖的 plan、request、result、analysis、evaluation 和 event。
- 没有改善时允许 keep/stop/human_review，不伪造正向结果。
