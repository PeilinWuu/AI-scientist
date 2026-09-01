# AI Scientist UI Localization Audit

审计日期：2026-09-01。范围仅限 `app_streamlit.py` 与现有 presentation renderer；API、Pydantic、
persisted JSON、benchmark 和 evidence 不在改写范围内。

| 页面 / 区域 | 审计前显示形式 | 直接 JSON | 英文暴露 | 推荐中文展示 | 保留 Raw JSON | 优先级 |
| --- | --- | --- | --- | --- | --- | --- |
| 首页标题与说明 | `AI Scientist` + 中英混合字段 | 否 | Research Question、Advanced Settings、Custom seed、Start Research | 中文主标签，保留产品名和必要技术词 | 不适用 | P0 |
| 项目阶段与执行能力 | 阶段部分已中文；执行能力为英文句子，外部等待突出 enum | 否 | Internal/External/Planning | 中文阶段和完整行动提示；enum 仅开发者详情 | 是 | P0 |
| 内部执行闭环 | 指标卡 + `st.json` 展示 FeedbackSignal / PlanAdjustment / IterationRecord | 是 | 字段名和值混合英文 | ExecutionResult 卡片、反馈摘要、old/new 调整表、两轮对比 | 是，默认折叠 | P0 |
| 上传资料 | 逐文件卡片，中英混合添加类型；解析摘要为英文 parser 文本 | 否 | Add Research Material / Add Experimental Result | 中文类型、行列数、解析状态、来源；校验信息放审计详情 | 是 | P0 |
| 证据与来源 | 基本为中文列表；状态值和 Evidence 字样仍有英文 | 否 | Evidence、unknown 等 | 中文来源卡和状态标签 | 是 | P1 |
| 主张—证据映射 | 人类可读列表，但 claim status 直接显示内部值 | 否 | supported / unknown | 中文状态与证据引用表 | 是 | P1 |
| 假设、方法、研究设计、分析方案 | 已是中文段落 / 列表；部分枚举值未映射 | 否 | research mode enum | 使用集中式字段和枚举映射 | 是 | P1 |
| 独立审查 | 中文指标和问题列表，decision 只做局部判断 | 否 | decision enum 隐含 | 中文决定标签、总体评分、问题和建议 | 是 | P0 |
| 修订历史 | 大部分中文，标题仍含 Independent Review / Reviewer | 否 | Independent Review、Reviewer | 中文标题和建议标签 | 是 | P1 |
| 最终综合 | 逐字段列表；未知字段统一落为“其他说明” | 否 | 模型内容可能为英文 | 固定中文章节顺序与 deterministic 字段映射 | 是 | P0 |
| 错误与异步任务 | 大部分已有安全中文；个别 API detail 直接输出 | 否 | exception detail 可能英文 | 错误码确定性映射；详情仅开发者折叠区 | 是 | P0 |
| 能力清单 | 普通页面直接 `st.write(list)`，视觉接近 Python list | 间接 | executor ID | 中文项目符号列表；原 ID 放开发者详情 | 是 | P1 |
| 事件与 artifact | 默认中文事件；开发者模式 dataframe | 否 | 内部字段 | 默认时间线，原始事件 / artifact 仅开发者详情 | 是 | P2 |

## 搜索结果

- `st.json`：审计前普通用户路径仅发现内部执行闭环一处，必须替换为中文 renderer。
- `model_dump*`：只用于 API / renderer 输入规范化，没有直接输出到普通页面。
- `render_debug_object`：主要位于开发者模式或显式开发者折叠区；保留但不得作为普通错误默认输出。
- `st.dataframe`：模型测试表、调试记录和 artifact 表；普通用户表格需要中文列名。
- persisted artifact 与 API JSON 必须保持英文 schema key 和完整原始值。

## 实施边界

1. 所有翻译均为确定性 label / view-model 转换，不调用 LLM 重算或解释指标。
2. Round 1、Round 2、RMSE、Qwen、API、seed 等技术词可保留。
3. 原始结构化数据统一放入默认收起的“查看原始结构化数据（开发者）”。
4. 用户语言偏好只影响自然语言 prompt 提示，不改变 schema 字段、引用原文、DOI、URL 或程序数值。

## 验收结论

- 首页、项目状态、上传入口、能力列表、审查与修订标题已使用中文主标签。
- 内部执行闭环已展示第一轮结果、真实反馈、精确 old/new 调整、第二轮结果与产物；原始 JSON 默认折叠。
- 外部实验路径明确说明系统不伪造实验结果，并保留研究者上传文件的来源、轮次和解析记录。
- 中文科学问题会向所有研究角色传递简体中文输出要求；显式英文请求仍可覆盖。
- 内置浏览器已分别验收内部确定性执行项目和外部实验结果项目；普通页面未出现 Python 列表或展开的原始 JSON。
