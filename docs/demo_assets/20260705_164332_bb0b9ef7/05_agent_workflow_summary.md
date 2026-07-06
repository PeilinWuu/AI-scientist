# Agent Workflow Summary

1. ProblemAnalystAgent parses the goal, variables, constraints, and user feedback.
2. ExperimentPlannerAgent creates concrete parameter candidates for the next virtual experiment.
3. SoftSwimmerSimulator executes the lightweight virtual experiment backend.
4. DataAnalystAgent ranks candidates, finds the best design, and summarizes failures.
5. CriticAgent converts measured failures into next-round planning instructions.
6. ReportWriterAgent produces the final Markdown and JSON report.

PPT message: The main contribution is the closed-loop planning and feedback mechanism for direction 1B.
