"""Backend verification of a model answer. The model's declarations are never trusted as-is.

Rules (each violation is repaired AND reported in `issues`; nothing is silently accepted):
* A citation id that was not provided in this request is removed ("invalid_citation"). Ids are never invented.
* A claim with basis `evidence` that has no valid E-citation is downgraded to `unsupported`.
* A claim with basis `patient_record` needs a valid F-citation, else it is downgraded.
* `general_knowledge` claims carry no citations (any are removed); research text cannot be used to back them.
* In modes without retrieval (gk), `evidence` claims are impossible: downgraded.
* A research citation on a `patient_record` claim (or a fact citation on an `evidence` claim) is removed: patient facts
  and research findings stay separate.
* Claims that give dosing or individual treatment directions are dropped.
* `conflicts_with` ids must be valid evidence ids; claims declaring a conflict are flagged.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.answering.safety import screen_claim_text
from app.answering.schemas import (
    AnswerBody,
    AnswerIssue,
    CitationOut,
    ClaimOut,
    EvidenceItem,
    Mode,
    ModelAnswer,
    PatientFact,
)


@dataclass
class ValidationResult:
    body: AnswerBody
    issues: list[AnswerIssue] = field(default_factory=list)

    @property
    def cited_evidence_ids(self) -> list[str]:
        return [c.citation_id for c in self.body.citations if c.kind == "research_chunk"]


def validate_answer(answer: ModelAnswer, *, mode: Mode, evidence: list[EvidenceItem], facts: list[PatientFact],
                    retrieval_performed: bool) -> ValidationResult:
    ev = {e.citation_id: e for e in evidence}
    fx = {f.citation_id: f for f in facts}
    issues: list[AnswerIssue] = []
    claims_out: list[ClaimOut] = []
    supports: dict[str, list[int]] = {}

    for model_claim in answer.claims:
        idx = len(claims_out)
        flags: list[str] = []
        reason = screen_claim_text(model_claim.text)
        if reason:
            issues.append(AnswerIssue(code=f"removed_{reason}", claim_index=None,
                                      message="A statement giving dosing or individual treatment direction was removed."))
            continue

        basis = model_claim.basis
        valid_e = [c for c in dict.fromkeys(model_claim.citations) if c in ev]
        valid_f = [c for c in dict.fromkeys(model_claim.citations) if c in fx]
        invalid = [c for c in dict.fromkeys(model_claim.citations) if c not in ev and c not in fx]
        if invalid:
            flags.append("invalid_citation_removed")
            issues.append(AnswerIssue(code="invalid_citation", claim_index=idx,
                                      message=f"Citation(s) {', '.join(invalid)} were not provided in this request and were removed."))

        if basis == "evidence":
            if not retrieval_performed or mode == "gk":
                flags.append("downgraded_no_retrieval")
                issues.append(AnswerIssue(code="evidence_claim_without_retrieval", claim_index=idx,
                                          message="The claim was marked as research-based but no research was retrieved."))
                basis, citations = "unsupported", []
            elif not valid_e:
                flags.append("downgraded_missing_citation")
                issues.append(AnswerIssue(code="unsupported_claim", claim_index=idx,
                                          message="The claim was marked as research-based but cites no retrieved passage."))
                basis, citations = "unsupported", []
            else:
                citations = valid_e
                if valid_f:
                    flags.append("patient_citation_removed")
        elif basis == "patient_record":
            if not valid_f:
                flags.append("downgraded_missing_citation")
                issues.append(AnswerIssue(code="unsupported_patient_fact", claim_index=idx,
                                          message="The claim was marked as a patient fact but cites no case-record fact."))
                basis, citations = "unsupported", []
            else:
                citations = valid_f
                if valid_e:
                    flags.append("research_citation_removed")
                    issues.append(AnswerIssue(code="research_cited_for_patient_fact", claim_index=idx,
                                              message="A research citation was removed from a patient-record claim."))
        elif basis == "general_knowledge":
            citations = []
            if model_claim.citations:
                flags.append("citation_removed_from_general_knowledge")
                issues.append(AnswerIssue(code="citation_on_general_knowledge", claim_index=idx,
                                          message="Citations were removed: general knowledge is not backed by the retrieved documents."))
        else:  # unsupported
            citations = []

        conflicts = [c for c in dict.fromkeys(model_claim.conflicts_with) if c in ev]
        if len(conflicts) != len(set(model_claim.conflicts_with)):
            issues.append(AnswerIssue(code="invalid_conflict_reference", claim_index=idx,
                                      message="A conflicting-evidence reference was not a retrieved passage and was removed."))
        if conflicts:
            flags.append("conflicting_evidence")
        if basis == "unsupported":
            flags.append("unsupported")

        claims_out.append(ClaimOut(index=idx, text=model_claim.text, basis=basis, citations=citations,
                                   conflicts_with=conflicts, flags=flags))
        for c in citations + conflicts:
            supports.setdefault(c, []).append(idx)

    citations_out: list[CitationOut] = []
    for cid in sorted(supports, key=lambda c: (c[0], int(c[1:]))):
        idxs = sorted(set(supports[cid]))
        if cid in ev:
            e = ev[cid]
            citations_out.append(CitationOut(
                citation_id=cid, kind="research_chunk", supports_claims=idxs, chunk_id=e.chunk_id,
                document_id=e.document_id, document_title=e.document_title, section=e.section,
                page_start=e.page_start, page_end=e.page_end, location=e.location, similarity=e.similarity,
                content_hash=e.content_hash, excerpt=e.matched_passage[:300]))
        else:
            f = fx[cid]
            citations_out.append(CitationOut(citation_id=cid, kind="patient_fact", supports_claims=idxs,
                                             fact_label=f.label, fact_value=f.value))

    limitations = list(dict.fromkeys(answer.limitations))
    if not claims_out:
        limitations.append("No usable statement remained after the safety and citation checks.")
        issues.append(AnswerIssue(code="no_usable_claims", message="Every statement was removed by the backend checks."))
    unsupported = sum(1 for c in claims_out if c.basis == "unsupported")
    if mode in ("rag", "both") and retrieval_performed and not any(c.basis == "evidence" for c in claims_out):
        limitations.append("None of the retrieved passages could be used to support a claim in this answer.")
    body = AnswerBody(
        claims=claims_out, limitations=limitations, citations=citations_out, unsupported_claim_count=unsupported,
        has_conflicting_evidence=any(c.conflicts_with for c in claims_out))
    return ValidationResult(body=body, issues=issues)
