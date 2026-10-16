"""Deterministic safeguards, enforced by the backend before and after the model is called.

This is an educational research assistant, not a clinical decision-maker. The backend therefore:
* detects urgent/dangerous wording and answers with an escalation message (no model call needed);
* refuses requests to diagnose, prescribe, or choose an individual's treatment (no model call);
* removes dosing instructions and individual treatment directions from model output.

These are keyword rules: simple, auditable, and not exhaustive. They complement, and never replace,
the instructions given to the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Refusal = Literal["diagnosis", "prescription", "treatment_selection"]

CLINICAL_DISCLAIMER = (
    "Educational use only. This tool summarizes research documents and does not diagnose, prescribe, or choose "
    "treatment for any individual. Findings must be interpreted by a qualified clinician.")

ESCALATION_MESSAGE = (
    "If this concerns a real person who may be in danger (thoughts of suicide or self-harm, a suspected overdose, "
    "severe withdrawal such as seizures or confusion, or risk to others), do not use this tool. Contact emergency "
    "services (for example 112 in the EU or 911 in the US), a local crisis line, or a qualified clinician right away. "
    "This application cannot assess or respond to emergencies. The cases here are fictional, but the same steps apply "
    "to a real situation.")

REFUSAL_MESSAGES: dict[str, str] = {
    "diagnosis": ("I can't diagnose a patient or say whether they meet criteria for a disorder. I can summarize what "
                  "the research documents say about assessment approaches; a qualified clinician makes any diagnosis."),
    "prescription": ("I can't recommend medications, doses, or prescriptions for a patient. I can describe what the "
                     "research documents report about medication options and their limitations, for a qualified "
                     "clinician to interpret."),
    "treatment_selection": ("I can't choose an individual treatment plan for a patient. I can describe research "
                            "approaches and their limitations that a qualified clinician might consider."),
}

_URGENT = [
    r"\bsuicid", r"\bkill(ing)? (myself|himself|herself|themselves|me)\b", r"\bend(ing)? (my|his|her|their) life\b",
    r"\bwant(s)? to die\b", r"\bself[- ]?harm", r"\bhurt(ing)? (himself|herself|themselves|myself|others|someone)\b",
    r"\boverdos(e|ed|ing)\b.{0,30}\b(now|right now|just|currently|tonight|today)\b",
    r"\b(is|are|am) (currently )?(overdosing|unresponsive|not breathing|unconscious)\b",
    r"\bnot breathing\b", r"\bunresponsive\b", r"\bseizure", r"\bconvuls", r"\bdelirium tremens\b",
    r"\bsevere withdrawal\b", r"\bemergency\b", r"\bmedical emergency\b", r"\bin danger\b",
]
_URGENT_RE = re.compile("|".join(_URGENT), re.I)

_PROHIBITED: list[tuple[Refusal, re.Pattern]] = [
    ("diagnosis", re.compile(
        r"\bdiagnos(e|ing)\b|\bdoes (he|she|they|the patient|this patient) (have|suffer)\b|"
        r"\bmeets? (the )?(dsm|icd)?\s*criteria\b|\bis (he|she|they|this patient) an? (alcoholic|addict|junkie)\b", re.I)),
    ("prescription", re.compile(
        r"\bprescribe\b|\bprescription\b|\bwhat (dose|dosage)\b|\bhow (much|many) (mg|milligrams|pills|tablets)\b|"
        r"\bdosage\b|\bwhich (medication|drug|medicine)s? should (he|she|they|the patient|i|we)\b|"
        r"\bshould (he|she|they|the patient|i|we) (take|start|be (given|prescribed|put on)|use)\b.{0,40}\b(medication|drug|"
        r"methadone|buprenorphine|naltrexone|acamprosate|disulfiram|benzodiazepine|diazepam)", re.I)),
    ("treatment_selection", re.compile(
        r"\bwhat (treatment|therapy|intervention|plan) should (he|she|they|the patient|i|we) (get|have|receive|choose|use|follow)\b|"
        r"\b(best|right|ideal) (treatment|therapy|intervention|plan) for (him|her|them|this patient|p-\d{3})\b|"
        r"\bwhich (treatment|therapy|intervention) (is|would be) (best|right) for (him|her|them|this patient|p-\d{3})\b|"
        r"\bchoose (a|the) treatment (for|plan)\b|\btreatment plan for (him|her|them|this patient|p-\d{3})\b", re.I)),
]


@dataclass(frozen=True)
class SafetyAssessment:
    urgent: bool
    refusal: Refusal | None
    matched: str | None = None


def assess_question(question: str) -> SafetyAssessment:
    """Pre-check on the student's question."""
    m = _URGENT_RE.search(question)
    refusal = next((kind for kind, rx in _PROHIBITED if rx.search(question)), None)
    return SafetyAssessment(urgent=bool(m), refusal=refusal, matched=m.group(0) if m else None)


# ---- output screening ----
_DOSE = re.compile(r"\b\d+(?:[.,]\d+)?\s?(?:mg|mcg|µg|ug|g|ml|mL|iu|IU|units?)\b(?:\s*(?:/|per)\s*(?:day|dose|kg|hour|h))?")
_DIRECTIVE = re.compile(
    r"\b(?:the patient|he|she|they|p-\d{3})\s+(?:should|must|needs? to|ought to)\s+"
    r"(?:start|begin|take|be (?:given|prescribed|put on|started on)|switch to|stop taking|increase|reduce|use)\b|"
    r"\bI (?:recommend|advise|suggest|prescribe)\b|\bwe recommend (?:that )?(?:he|she|they|the patient)\b|"
    r"\b(?:you|he|she|they) (?:should|must) (?:take|start|stop)\b", re.I)


def screen_claim_text(text: str) -> str | None:
    """Return a reason when a model-written claim gives dosing or individual treatment directions."""
    if _DOSE.search(text):
        return "dosing_instruction"
    if _DIRECTIVE.search(text):
        return "individual_treatment_direction"
    return None
