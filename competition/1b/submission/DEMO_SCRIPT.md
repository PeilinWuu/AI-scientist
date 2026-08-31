# 5–8 Minute Demo Script

1. **0:00–0:40 — 首页**：打开 Competition Demo；说明数值由程序计算、Qwen 与执行器边界。若 API 未启动，切换到已生成 `benchmark_summary.md`。
2. **0:40–1:20 — 问题与 Round 1**：展示阻尼振子问题、硬约束、Round 1 宽范围粗网格。
3. **1:20–2:20 — 点击完整运行**：seed 20260831；预期状态 `complete`，RMSE 0.057684→0.033089。
4. **2:20–3:15 — 原始数据和图**：展示观测、Round 1、Round 2 PNG；说明图来自 artifact endpoint。
5. **3:15–4:10 — Feedback/diff**：展示 old/new 范围、`derived_from_execution_id` 和 evidence refs，强调不是重新生成方案。
6. **4:10–5:00 — Baseline**：展示 184 vs 182 次评价，反馈 RMSE 0.033089，baseline 0.050483；再展示 5-seed 5/5 获胜。
7. **5:00–5:45 — 失败案例**：点击失败检查；展示缺文件、非法 operation、参数越界、路径逃逸，最后一项 human_review。
8. **5:45–6:30 — 审计**：展开事件和 checksum；打开 `/docs` 展示 API。
9. **6:30–7:15 — Qwen 与边界**：展示脱敏 authenticated smoke evidence：`qwen3.8-max`、`QWEN_SMOKE_OK`、状态 PASSED；强调不保存密钥。
10. **7:15–8:00 — 复现**：展示一键命令和 readiness。运行失败时 fallback 为仓库内已保存 JSON/PNG，不口头补造结果。
