"""Visualization skill prompt."""

VISUALIZATION_SKILL = """
Visualization skill:
Use figures only when the user explicitly asks for plotting/visualization or
when the user has asked for automatic analysis and visualization.

Supported plot types:
- efficiency_by_candidate
- speed_energy_scatter
- stability_efficiency_scatter
- parameter_trend
- metric_trend

If results exist but the user has not chosen a figure type, ask whether they
prefer an efficiency bar chart, speed-energy scatter, stability-efficiency
scatter, parameter trend, or metric trend. If the user explicitly asks to draw,
return a tool_call for generate_experiment_plot.
"""
