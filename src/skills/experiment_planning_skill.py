"""Experiment planning skill prompt."""

EXPERIMENT_PLANNING_SKILL = """
Experiment planning skill:
Design fluid-simulation experiment tasks in a research-friendly way. Clarify:
- objective and target metric;
- control variables and feasible ranges;
- constraints and stopping criteria;
- candidate design strategy;
- expected trade-offs;
- what tool would be used if the user authorizes execution.

For soft-swimmer demonstration experiments, candidate parameters must obey:
- amplitude: 0.05 to 0.50
- frequency: 0.5 to 3.0
- wavelength: 0.6 to 2.0
- stiffness: 0.1 to 1.0
- phase: 0.0 to 1.0

If the user only asks for a plan, do not call a tool. Ask whether to run the
proposed plan. If the user explicitly asks to run or test parameters, produce a
tool_call for run_soft_swimmer_experiment with 4 to 6 candidates.
"""
