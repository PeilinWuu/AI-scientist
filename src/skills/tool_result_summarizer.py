"""Human-readable summaries for tool results."""

from __future__ import annotations

from typing import Any


DISPLAY_COLUMNS = [
    "candidate_id",
    "amplitude",
    "frequency",
    "wavelength",
    "stiffness",
    "phase",
    "mean_speed",
    "energy_cost",
    "efficiency",
    "stability_score",
    "constraint_violation",
]


def summarize_soft_swimmer_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return summary, table rows, and suggested next actions for experiment results."""

    rows = [
        {key: row.get(key) for key in DISPLAY_COLUMNS if key in row}
        for row in result.get("results", [])
        if isinstance(row, dict)
    ]
    best = result.get("best_candidate") or {}
    best_id = best.get("candidate_id", "unknown")
    efficiency = _fmt(best.get("efficiency"))
    speed = _fmt(best.get("mean_speed"))
    energy = _fmt(best.get("energy_cost"))
    stability = _fmt(best.get("stability_score"))
    violated = [row.get("candidate_id") for row in rows if row.get("constraint_violation")]

    conclusion = (
        f"我测试了 {len(rows)} 组候选参数。当前最佳候选是 {best_id}，"
        f"efficiency={efficiency}，mean_speed={speed}，energy_cost={energy}，"
        f"stability_score={stability}。"
    )
    constraint_text = (
        "所有候选都满足当前约束。"
        if not violated
        else f"以下候选存在约束违反：{', '.join(map(str, violated))}。"
    )
    next_step = (
        "下一步可以围绕最佳候选做局部参数搜索，或生成效率柱状图、速度-能耗散点图来观察权衡关系。"
    )
    return {
        "summary": f"{conclusion} {constraint_text} {next_step}",
        "sections": [
            {"title": "简短结论", "content": conclusion},
            {"title": "约束状态", "content": constraint_text},
            {"title": "下一步建议", "content": next_step},
        ],
        "tables": [{"title": "候选参数与指标", "rows": rows}],
        "suggested_actions": [
            {"label": "画效率柱状图", "action": "plot_efficiency"},
            {"label": "画速度-能耗散点图", "action": "plot_speed_energy"},
            {"label": "继续下一轮实验", "action": "continue_next_round"},
        ],
    }


def _fmt(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"
