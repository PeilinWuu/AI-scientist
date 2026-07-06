# FlowScientist-Loop：面向软体游动流场优化的实验任务规划与反馈迭代智能体

说明：本大纲面向比赛方向 1B：科学实验任务规划与反馈迭代。不要把展示重点写成方向 A 的一次性科学假设生成。

## 1. 项目名称与赛题方向

- 应放截图：项目首页或标题页，标注 FlowScientist-Loop 和方向 1B。
- 应讲的核心句子：本项目展示 AI 如何围绕实验结果持续规划下一轮实验，而不是只生成一次性假设。

## 2. 研究问题：软体游动流场优化

- 应放截图：软体游动机器人示意图或项目变量表。
- 应讲的核心句子：软体游动机器人需要在推进速度、能耗、稳定性和流场损失之间做多目标权衡。

## 3. 为什么需要实验任务规划与反馈迭代

- 应放截图：传统试错流程与闭环流程对比图。
- 应讲的核心句子：单轮建议无法利用实验结果，闭环规划能把失败原因转化为下一轮可执行实验。

## 4. 系统闭环总览

- 应放截图：research goal 到 final report 的闭环流程图。
- 应讲的核心句子：主线是 research goal -> experiment planning -> virtual experiment execution -> data analysis -> critique feedback -> next-round planning -> final report。

## 5. 多智能体架构

- 应放截图：ProblemAnalystAgent、ExperimentPlannerAgent、DataAnalystAgent、CriticAgent、ReportWriterAgent 的结构图。
- 应讲的核心句子：每个 agent 负责闭环中的一个清晰职责，减少单体提示词不可控的问题。

## 6. 实验变量与约束

- 应放截图：Streamlit 约束输入区或 `Constraints` schema。
- 应讲的核心句子：实验变量包括 amplitude、frequency、wavelength、stiffness、phase，约束包括最低稳定性和最大能耗。

## 7. 虚拟实验器设计

- 应放截图：`src/simulator/soft_swimmer_simulator.py` 关键公式。
- 应讲的核心句子：当前版本使用 lightweight virtual experiment backend，保留基本物理趋势并支持可复现噪声。

## 8. 反馈迭代机制

- 应放截图：`experiment_planner.py` 和 `critic.py` 中的反馈规则。
- 应讲的核心句子：能耗过高就降低频率或幅值，稳定性过低就降低幅值或提高刚度，速度过低就提高频率或幅值。

## 9. 后端 API

- 应放截图：FastAPI `/docs` 页面。
- 应讲的核心句子：后端提供运行实验闭环、读取 run 日志、读取报告、追加人工反馈的接口。

## 10. 前端页面

- 应放截图：Streamlit 主页面，包括输入区、结果表、效率曲线和最终报告。
- 应讲的核心句子：前端用于比赛演示完整实验闭环和每轮证据链。

## 11. 案例一：效率优先

- 应放截图：`case_efficiency_first.json` 和对应效率曲线。
- 应讲的核心句子：当目标是最大化效率时，系统会围绕高效率候选做局部搜索。

## 12. 案例二：低能耗优先

- 应放截图：`case_low_energy.json` 和结果表。
- 应讲的核心句子：当目标偏向低能耗时，规划策略会更倾向降低频率和幅值。

## 13. 案例三：人工反馈改变计划

- 应放截图：`case_human_feedback.json` 或 human feedback 后的 next plan preview。
- 应讲的核心句子：人工反馈可以改变下一轮规划重点，例如更重视稳定性。

## 14. 结果与最优候选参数

- 应放截图：`final_report.md` 的 Best Candidate Design 或 `04_best_candidate_table.csv`。
- 应讲的核心句子：系统输出的最优候选是虚拟筛选结果，适合作为后续高保真仿真的输入。

## 15. 局限性与 FreeFlow/CFD 接入计划

- 应放截图：`freeflow_csv_adapter.py` 或 adapter 接口图。
- 应讲的核心句子：只要 FreeFlow/CFD 输出相同 schema，就能替换 simulator，而不需要重写 agent workflow、API 和 UI。

## 16. 总结与可复现说明

- 应放截图：`run_examples.py` 输出、`runs/{run_id}/` 文件结构、demo assets 文件夹。
- 应讲的核心句子：项目可在无 API key 的 Mock 模式下复现，也预留 Qwen API 连通性检查和真实仿真接入路径。
