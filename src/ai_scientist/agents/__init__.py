"""Structured role agents used only by ResearchOrchestrator."""

from src.ai_scientist.agents.analyst import AnalystAgent
from src.ai_scientist.agents.evidence_researcher import EvidenceResearcherAgent
from src.ai_scientist.agents.hypothesis_scientist import HypothesisScientistAgent
from src.ai_scientist.agents.methodologist import MethodologistAgent
from src.ai_scientist.agents.reproducibility_engineer import ReproducibilityEngineerAgent
from src.ai_scientist.agents.research_director import ResearchDirectorAgent
from src.ai_scientist.agents.scientific_synthesizer import ScientificSynthesizerAgent
from src.ai_scientist.agents.skeptical_reviewer import SkepticalReviewerAgent
from src.ai_scientist.agents.study_designer import StudyDesignerAgent

__all__ = [
    "AnalystAgent",
    "EvidenceResearcherAgent",
    "HypothesisScientistAgent",
    "MethodologistAgent",
    "ReproducibilityEngineerAgent",
    "ResearchDirectorAgent",
    "ScientificSynthesizerAgent",
    "SkepticalReviewerAgent",
    "StudyDesignerAgent",
]
