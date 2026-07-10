"""Base class that loads only the selected skills for one structured role call."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

from src.ai_scientist.schemas import ResearchProject
from src.ai_scientist.skill_loader import SkillLoader
from src.ai_scientist.structured_client import StructuredCallMetadata, StructuredQwenClient


OutputT = TypeVar("OutputT", bound=BaseModel)


@dataclass
class AgentRun(Generic[OutputT]):
    output: OutputT
    metadata: StructuredCallMetadata
    tool_names: list[str] = field(default_factory=list)
    auxiliary: dict[str, Any] = field(default_factory=dict)


class BaseResearchAgent(Generic[OutputT]):
    agent_name: str
    output_model: type[OutputT]

    def __init__(
        self,
        client: StructuredQwenClient | None = None,
        skill_loader: SkillLoader | None = None,
    ) -> None:
        self.client = client or StructuredQwenClient()
        self.skill_loader = skill_loader or SkillLoader()

    def run(self, project: ResearchProject) -> AgentRun[OutputT]:
        domain_skill = "general" if project.domain == "mathematics" else project.domain
        skills = self.skill_loader.load_for_agent(
            self.agent_name,
            project.research_mode,
            domain_skill,
        )
        result = self.client.call(
            agent_name=self.agent_name,
            instructions=self.skill_loader.compose_instructions(skills),
            payload=self.build_payload(project),
            output_model=self.output_model,
        )
        return AgentRun(output=result.value, metadata=result.metadata)

    def build_payload(self, project: ResearchProject) -> dict[str, Any]:
        raise NotImplementedError


def project_snapshot(project: ResearchProject, fields: list[str]) -> dict[str, Any]:
    """Pass only named structured project fields to a role."""

    dumped = project.model_dump(mode="json")
    return {field: dumped.get(field) for field in fields}
