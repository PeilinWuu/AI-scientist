from pathlib import Path

import yaml

from src.ai_scientist.domain_router import DomainRouter
from src.ai_scientist.method_selector import MethodSelector
from src.ai_scientist.schemas import ResearchMode, ResearchQuestion
from src.ai_scientist.skill_loader import REQUIRED_SKILL_FIELDS, SkillLoader


SKILLS_ROOT = Path("src/ai_scientist/skills")


def question(text: str) -> ResearchQuestion:
    return ResearchQuestion(
        original_question=text,
        normalized_question=text,
        objective=text,
    )


def test_every_skill_has_required_fields() -> None:
    files = list(SKILLS_ROOT.rglob("*.yaml"))
    assert len(files) == 28
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert REQUIRED_SKILL_FIELDS <= data.keys(), path


def test_core_and_generic_workflow_are_domain_neutral() -> None:
    paths = [*Path("src/ai_scientist/skills/core").glob("*.yaml")]
    paths += [Path("src/ai_scientist/schemas.py"), Path("src/ai_scientist/workflows/general_research_v1.yaml")]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths).lower()
    forbidden = [
        "lbm", "cfd", "soft-swimmer", "圆柱绕流", "reynolds number",
        "cd_mean", "cl_rms", "strouhal", "vorticity", "网格无关性",
    ]
    for term in forbidden:
        assert term not in text


def test_loader_loads_exactly_four_selected_skills() -> None:
    skills = SkillLoader().load_for_agent(
        "study_designer", ResearchMode.COMPUTATIONAL_EXPERIMENT, "computer_science"
    )
    assert [item["name"] for item in skills] == [
        "epistemic_policy", "study_designer", "computational_experiment", "computer_science"
    ]


def test_cross_domain_method_selection() -> None:
    selector = MethodSelector()
    cases = [
        ("研究某类非线性偏微分方程弱解的唯一性条件。", ResearchMode.THEORETICAL),
        ("远程办公是否提高软件工程师生产率？", ResearchMode.OBSERVATIONAL),
        ("评估一种新分类算法是否优于现有方法。", ResearchMode.COMPUTATIONAL_EXPERIMENT),
        ("设计一种提高催化反应选择性的研究方案。", ResearchMode.CONTROLLED_EXPERIMENT),
        ("系统整理微塑料对海洋生态系统影响的证据。", ResearchMode.SYSTEMATIC_REVIEW),
    ]
    for text, expected in cases:
        result = selector.select(question(text), text, [], ["web_search", "artifact_store"], {})
        assert result.primary_research_mode == expected

    fluid = "研究圆柱尾流被动控制结构对涡脱落的影响。"
    result = selector.select(question(fluid), fluid, [], ["web_search"], {})
    assert result.primary_research_mode in {
        ResearchMode.CONTROLLED_EXPERIMENT,
        ResearchMode.SIMULATION,
    }


def test_domain_router_uses_fluid_plugin_only_when_selected() -> None:
    router = DomainRouter()
    fluid = router.route(question("研究圆柱尾流被动控制结构对涡脱落的影响。"))
    math = router.route(question("研究非线性偏微分方程弱解的唯一性条件。"))

    assert fluid.primary_domain == "fluid_dynamics"
    assert fluid.selected_domain_skills == ["fluid_dynamics"]
    assert math.primary_domain == "mathematics"
    assert math.selected_domain_skills == ["general"]
