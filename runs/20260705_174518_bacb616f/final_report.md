# FlowScientist-Loop Report

## LLM Backend
- Provider: Qwen via Alibaba Cloud Model Studio / Bailian compatible API
- Model: qwen-turbo
- Transport: curl fallback
- Mock mode: false

## 1. Project Title
FlowScientist-Loop: Scientific Experiment Task Planning and Feedback Iteration

## 2. Problem Statement
Soft robotic swimmer design requires balancing propulsion speed, energy cost, stability, and flow loss. This prototype demonstrates a closed experiment loop rather than one-shot hypothesis generation.

## 3. Research Goal
????????????????????????????

## 4. Experimental Variables and Constraints
- Variables: amplitude, frequency, wavelength, stiffness, phase.
- Minimum stability: 0.72
- Maximum energy cost: 2.0
- Target metric: efficiency
- Human feedback: ??????

## 5. Agent Workflow
ProblemAnalystAgent parses the goal and constraints. ExperimentPlannerAgent proposes parameter combinations. SimulationExecutor runs the lightweight virtual experiment. DataAnalystAgent selects the best measured design and identifies failures. CriticAgent converts failures into next-step guidance. ReportWriterAgent summarizes the complete loop.

## 6. Iteration History
### Iteration 1
- Plan strategy: Structured first pass over balanced, speed, flexibility, and stability regimes.
- Best candidate: iter1_cand1 with efficiency=2.48443, mean_speed=0.84739, energy_cost=0.32664, stability_score=0.96546.
- Analysis: Best candidate is iter1_cand1 by efficiency. Mean efficiency this round is 1.88296, with 6/6 feasible candidates. Main issue: none
- Feedback: Next planner should continue local search around the best measured candidate. This links measured results directly to the next experiment plan.

### Iteration 2
- Plan strategy: Feedback-aware local search around the best feasible design. Main adjustment: continue local search around the best measured candidate.
- Best candidate: iter2_cand4 with efficiency=2.57606, mean_speed=0.89606, energy_cost=0.33212, stability_score=0.96192.
- Analysis: Best candidate is iter2_cand4 by efficiency. Mean efficiency this round is 2.50858, with 4/4 feasible candidates. Main issue: none
- Feedback: Next planner should continue local search around the best measured candidate. This links measured results directly to the next experiment plan.

### Iteration 3
- Plan strategy: Feedback-aware local search around the best feasible design. Main adjustment: continue local search around the best measured candidate.
- Best candidate: iter3_cand1 with efficiency=2.72927, mean_speed=0.89552, energy_cost=0.31414, stability_score=0.96716.
- Analysis: Best candidate is iter3_cand1 by efficiency. Mean efficiency this round is 2.60225, with 4/4 feasible candidates. Main issue: none
- Feedback: Next planner should continue local search around the best measured candidate. This links measured results directly to the next experiment plan.

## 7. Best Candidate Design
Iteration 3, candidate iter3_cand1: amplitude=0.16, frequency=1.11, wavelength=1.16, stiffness=0.42, phase=0.2444, efficiency=2.72927, energy_cost=0.31414, stability_score=0.96716.

## 8. Failure Analysis
- Iteration 1: No hard constraint failure in this iteration.
- Iteration 2: No hard constraint failure in this iteration.
- Iteration 3: No hard constraint failure in this iteration.

## 9. Next-round Experimental Plan
The next round should continue local search around the best measured candidate. If connected to a higher-fidelity backend, the next plan should re-test the best local neighborhood with stricter convergence checks.

## 10. Methods
The current backend is a lightweight virtual experiment simulator. It encodes qualitative relationships between swimmer parameters and metrics, adds seeded noise for reproducibility, and applies hard energy/stability constraints.

## 11. Experiments
The loop ran 3 iterations. Each iteration saved a plan JSON file, a CSV result table, analysis JSON, and feedback JSON.

## 12. Results
Best measured target value was obtained in iteration 3. The selected design is reported above and should be treated as a virtual-screening candidate.

## 13. Limitations
This is not a high-fidelity CFD or FreeFlow simulation. The formulas are qualitative, the noise model is simple, and the reported candidate must be validated in a real simulator or experiment.

## 14. References
- [To be filled manually] Literature on soft robotic swimmers and flow-field optimization.
- [To be filled manually] Literature on AI Scientist and multi-agent scientific discovery.
