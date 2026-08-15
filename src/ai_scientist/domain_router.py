"""Conservative domain-plugin routing based on multiple question signals."""

from __future__ import annotations

from src.ai_scientist.schemas import DomainSelectionOutput, ResearchQuestion


class DomainRouter:
    """Select domain plugins without changing the epistemic policy."""

    SIGNALS = {
        "mathematics": ["theorem", "proof", "equation", "uniqueness", "定理", "证明", "方程", "唯一性"],
        "physics": ["quantum", "particle", "field theory", "量子", "粒子", "物理"],
        "chemistry": ["reaction", "catalyst", "molecule", "催化", "反应", "分子", "化学"],
        "biology": ["cell", "gene", "organism", "细胞", "基因", "生物"],
        "medicine": ["patient", "clinical", "disease", "患者", "临床", "疾病", "医学"],
        "computer_science": ["algorithm", "software", "classifier", "算法", "软件", "分类模型", "机器学习"],
        "social_science": ["survey", "productivity", "policy", "remote work", "生产率", "远程办公", "社会"],
        "engineering": ["prototype", "design", "system", "结构设计", "工程", "原型"],
        "fluid_dynamics": ["wake", "flow", "vortex", "fluid", "尾流", "流动", "流体", "涡"],
    }

    def route(self, question: ResearchQuestion, domain_hint: str | None = None) -> DomainSelectionOutput:
        text = " ".join(
            [question.original_question, question.normalized_question, question.objective, domain_hint or ""]
        ).lower()
        scores = {
            domain: sum(1 for signal in signals if signal in text)
            for domain, signals in self.SIGNALS.items()
        }
        if domain_hint and domain_hint in scores:
            scores[domain_hint] += 2
        ranked = sorted(scores, key=scores.get, reverse=True)
        top = ranked[0]
        top_score = scores[top]
        second_score = scores[ranked[1]]
        if top_score == 0:
            return DomainSelectionOutput(
                primary_domain="general",
                confidence=0.35,
                selected_domain_skills=["general"],
                clarification_needed=True,
                clarification_questions=["Which disciplinary assumptions or standards should govern this project?"],
            )
        confidence = min(0.95, 0.5 + 0.12 * top_score - 0.05 * second_score)
        skill = "general" if top == "mathematics" else top
        secondary = [domain for domain in ranked[1:3] if scores[domain] > 0]
        return DomainSelectionOutput(
            primary_domain=top,
            secondary_domains=secondary,
            confidence=confidence,
            selected_domain_skills=[skill],
            clarification_needed=confidence < 0.55,
            clarification_questions=(
                ["Please clarify the primary discipline and applicable standards."] if confidence < 0.55 else []
            ),
        )
