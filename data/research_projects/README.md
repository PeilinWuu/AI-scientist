# 展示项目

仓库随源码分发两个主要完整展示结果，以及一个较早的规划流程验收快照。

## Iris 完整执行结果

项目 ID：`project_f9374def244c4829b4b8d8c52f4d56d2`

- 题目：Iris 数据中萼片长度与花瓣长度线性关联的描述与物种间比较
- 最终阶段：`COMPLETED`
- 模型调用：38 次成功使用，39 次尝试
- 研究产物：65 个
- 证据：5 条
- 主张：30 条
- 假设：4 条
- 独立审查和复审：5 次

## 阻尼振子完整执行结果

项目 ID：`project_ef11806974554b058250cde26681bd16`

- 最终阶段：`COMPLETED`
- 受控执行器：`damped_oscillator_v1`
- Round 1 RMSE：`0.05768367179165266`
- Round 2 RMSE：`0.03308926031835558`
- 相对 RMSE 改进：`42.636695462330316%`
- 两轮评估：184 次

## Iris 规划流程验收快照

项目 ID：`project_18af4366b95d4688a2a4464f6bea11cc`。该项目验证规划、证据筛选、修订、人工批准和报告流程，不包含数据执行结果。

服务器拉取对应 Git 分支后，如果 `AI_SCIENTIST_PROJECTS_DIR` 使用默认值
`data/research_projects`，可在 Streamlit 的“加载已有项目 ID”中直接输入以上任一 ID。例如：

```text
project_f9374def244c4829b4b8d8c52f4d56d2
project_ef11806974554b058250cde26681bd16
```

这些目录包含项目状态、事件日志、正式研究产物、展示数据及确定性分析结果。已完成任务的
临时 `jobs/` 记录和 `controlled_python/` 沙箱工作区不随示例分发；其他运行时项目也继续被
`.gitignore` 排除，不应提交到 GitHub。
