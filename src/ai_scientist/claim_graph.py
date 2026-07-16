"""Traceability operations for claims and evidence."""

from __future__ import annotations

from src.ai_scientist.exceptions import InvalidEvidenceReferenceError
from src.ai_scientist.schemas import Claim, Conclusion, EvidenceItem


class ClaimGraph:
    """Maintain claim-evidence links while preserving contradictions."""

    def __init__(self, evidence: list[EvidenceItem] | None = None, claims: list[Claim] | None = None) -> None:
        self.evidence = {item.evidence_id: item for item in evidence or []}
        self.claims = {item.claim_id: item for item in claims or []}

    def add_evidence(self, evidence: EvidenceItem) -> None:
        self.evidence[evidence.evidence_id] = evidence

    def add_claim(self, claim: Claim) -> None:
        self.claims[claim.claim_id] = claim

    def link_support(self, claim_id: str, evidence_id: str) -> None:
        claim = self._claim(claim_id)
        self._evidence(evidence_id)
        if evidence_id not in claim.supporting_evidence_ids:
            claim.supporting_evidence_ids.append(evidence_id)

    def link_contradiction(self, claim_id: str, evidence_id: str) -> None:
        claim = self._claim(claim_id)
        self._evidence(evidence_id)
        if evidence_id not in claim.contradicting_evidence_ids:
            claim.contradicting_evidence_ids.append(evidence_id)

    def get_supporting_evidence(self, claim_id: str) -> list[EvidenceItem]:
        claim = self._claim(claim_id)
        return [self.evidence[item_id] for item_id in claim.supporting_evidence_ids if item_id in self.evidence]

    def get_contradicting_evidence(self, claim_id: str) -> list[EvidenceItem]:
        claim = self._claim(claim_id)
        return [self.evidence[item_id] for item_id in claim.contradicting_evidence_ids if item_id in self.evidence]

    def find_unsupported_claims(self) -> list[Claim]:
        return [
            claim
            for claim in self.claims.values()
            if claim.status in {"unsupported", "unknown"}
            or (claim.status == "supported" and not self.get_supporting_evidence(claim.claim_id))
        ]

    def validate(self) -> list[str]:
        errors: list[str] = []
        for claim in self.claims.values():
            missing = [item for item in claim.supporting_evidence_ids if item not in self.evidence]
            if missing:
                errors.append(f"Claim {claim.claim_id} references missing evidence: {missing}")
            if claim.status == "supported" and not claim.supporting_evidence_ids:
                errors.append(f"Supported claim {claim.claim_id} has no evidence.")
        return errors

    def validate_or_raise(self) -> None:
        """Raise a typed error for the first invalid evidence reference."""

        for claim in self.claims.values():
            for evidence_id in claim.supporting_evidence_ids + claim.contradicting_evidence_ids:
                if evidence_id not in self.evidence:
                    raise InvalidEvidenceReferenceError(claim.claim_id, evidence_id)

    def to_dict(self) -> dict:
        """Return a safe serializable graph without model prompts or provider data."""

        return {
            "evidence_ids": sorted(self.evidence),
            "claims": [
                {
                    "claim_id": claim.claim_id,
                    "statement": claim.statement,
                    "status": claim.status,
                    "supporting_evidence_ids": claim.supporting_evidence_ids,
                    "contradicting_evidence_ids": claim.contradicting_evidence_ids,
                }
                for claim in self.claims.values()
            ],
            "links": [
                {"claim_id": claim.claim_id, "evidence_id": evidence_id, "relation": "supports"}
                for claim in self.claims.values()
                for evidence_id in claim.supporting_evidence_ids
            ]
            + [
                {"claim_id": claim.claim_id, "evidence_id": evidence_id, "relation": "contradicts"}
                for claim in self.claims.values()
                for evidence_id in claim.contradicting_evidence_ids
            ],
        }

    def validate_conclusion_traceability(self, conclusion: Conclusion) -> list[str]:
        errors: list[str] = []
        for item in conclusion.supported_findings:
            if item.supporting_claim_ids:
                missing_or_unsupported = [
                    claim_id
                    for claim_id in item.supporting_claim_ids
                    if claim_id not in self.claims
                    or self.claims[claim_id].status not in {"supported", "partially_supported"}
                    or not self.get_supporting_evidence(claim_id)
                ]
                if missing_or_unsupported:
                    errors.append(item.statement)
                continue
            matching_claim = next(
                (
                    claim
                    for claim in self.claims.values()
                    if claim.statement == item.statement
                    and claim.status in {"supported", "partially_supported"}
                    and self.get_supporting_evidence(claim.claim_id)
                ),
                None,
            )
            if matching_claim is None:
                errors.append(item.statement)
        return errors

    def _claim(self, claim_id: str) -> Claim:
        if claim_id not in self.claims:
            raise KeyError(f"Unknown claim_id: {claim_id}")
        return self.claims[claim_id]

    def _evidence(self, evidence_id: str) -> EvidenceItem:
        if evidence_id not in self.evidence:
            raise KeyError(f"Unknown evidence_id: {evidence_id}")
        return self.evidence[evidence_id]
