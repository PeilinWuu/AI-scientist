# Competition 1B PPT Content Draft（20 页以内）

## 1. 作品信息与核心结果

FlowScientist：可审计科学实验任务规划与反馈迭代。5 个 seed 中反馈系统 5/5 优于一次性 baseline；Round 1→2 平均 RMSE 改善 41.27%。

## 2. 问题与目标

现有科研 Agent 常停留在“写方案”。目标是展示计划、实际执行、确定性分析、反馈、第二轮调整和停止判断的真实闭环。

## 3. 科学逻辑

问题→预注册 Round 1→真实观测→拟合与质量门→结构化 FeedbackSignal→old/new PlanAdjustment→Round 2→比较。

## 4. 数据与仿真条件

阻尼振子 `x(t)=A exp(-d t) cos(ωt+φ)+noise`；360 个样本，时长 12，噪声标准差 0.03，固定 seed；真实参数仅用于程序评价。

## 5. 评价方法

主指标为拟合 RMSE，辅以参数 L1 误差、约束满足、执行成功和网格评价次数；全部由 NumPy 程序计算。

## 6. 系统架构

FastAPI + Streamlit + Pydantic + 项目持久化 + 追加事件 + 白名单 ExecutionAdapter；现有 AI Scientist 规划链保持隔离。

## 7. Qwen 与上下文工程

Qwen 负责结构化规划与解释；程序负责数据和数值。已使用 Alibaba Cloud Bailian authenticated endpoint 和 `qwen3.8-max` 完成真实 smoke test，精确响应 `QWEN_SMOKE_OK`；脱敏证据随仓库交付。

## 8. 任务规划

Round 1 在 `[0.05,0.35] × [2.0,2.8]` 上执行 7×9 粗网格，计划在执行前保存。

## 9. 实际执行

执行请求记录 operation、输入 checksum、fingerprint、seed、实际参数、依赖版本、耗时、日志和 artifacts。

## 10. 数据分析与质量控制

执行器同时支持结构检查、缺失率、描述统计、相关性、最小二乘回归、直方图和散点图；拒绝越界与路径逃逸。

## 11. 反馈机制

Round 1 的最佳参数、RMSE、步长和边界状态进入 FeedbackSignal；第二轮计划必须引用 execution/artifact ID。

## 12. 失败处理与人工接管

实际测试缺失文件、非法 operation、参数越界和路径逃逸；全部结构化 rejected，路径逃逸进入 human_review。

## 13. 旗舰案例设定

选择阻尼振子参数辨识：本地可复现、科学变量清晰、无需伪造数值、能自然产生粗到细的反馈调整。

## 14. 第一轮计划

63 次评价；flagship seed 20260831 实测 RMSE 0.057684，最佳点位于内部，但未达到 0.04 质量阈值。

## 15. 第一轮执行结果

[SCREENSHOT REQUIRED: Streamlit 原始观测、Round 1 拟合与 RMSE]

## 16. 调整依据

围绕 Round 1 实测最优点，将范围缩至一个粗步长内并改用 11×11 网格；old/new 与 evidence refs 已持久化。

## 17. 第二轮变化与结果

flagship Round 2 RMSE 0.033089，较 Round 1 改善 42.64%，达到阈值；代价是新增 121 次评价且依赖模型设定。

## 18. Baseline / 消融

5-seed：Round 2 平均 0.032789，一次性近等预算 baseline 平均 0.049702；反馈方案优势 34.03%，5/5 获胜。

## 19. 总体结果、失败和边界

闭环、审计、失败保留和真实 Qwen authenticated smoke 均已运行；当前边界包括简化噪声模型和数值仿真非硬件实验。

## 20. 复现、API、前端与材料

一键案例、pytest、FastAPI `/docs`、Streamlit Competition Demo、readiness JSON 和 evidence index 均随仓库交付。

[SCREENSHOT REQUIRED: API OpenAPI 与 readiness 页面]
