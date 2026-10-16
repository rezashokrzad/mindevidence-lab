"""Deterministic question classification (no LLM call): fast, auditable, and exactly testable.

Classes
  patient_history_lookup  answerable from the synthetic case record alone
  research_question       needs the research documents (or general knowledge)
  comparison              relates the patient's case to research findings: needs BOTH sources
  unsupported             off-topic, an attempt to override the system rules, or a multi-patient comparison

Every result carries a short rule name, recorded in the execution trace as the decision reason.
These are keyword rules and will misclassify some phrasings; they decide only which sources are consulted,
never what the answer says.
"""
from __future__ import annotations

import re

from app.workflow.state import QuestionClass

_INJECTION = re.compile(
    r"ignore (all |any |the |your )?(previous|prior|above|earlier) (instructions|rules|messages)|"
    r"disregard (the |your )?(system|previous|above)|reveal (your |the )?(system )?(prompt|instructions)|"
    r"\byou are now\b|\bdeveloper mode\b|\bjailbreak\b|\bact as (?!a (student|researcher))|"
    r"pretend (that )?you (are|have)|override (the )?(safety|rules)|forget (all |your )?(rules|instructions)", re.I)

_DOMAIN = re.compile(
    r"addict|alcohol|drink|drug|substance|opioid|opiate|heroin|methadone|buprenorphine|naloxone|naltrexone|cannabis|"
    r"marijuana|stimulant|cocaine|amphetamine|gambl|gaming|betting|craving|relapse|abstinen|recover|withdrawal|detox|"
    r"treatment|therap|intervention|medication|counsel|trigger|motivation|patient|\bcase\b|history|symptom|"
    r"psychosocial|\bcbt\b|contingency|harm reduction|overdose|evidence|research|stud(y|ies)|guideline|trial|review|"
    r"cochrane|\bwho\b|nida|mhgap|dependence|use disorder|comorbid|co-occurring|anxiety|depress|trauma|sleep", re.I)

_PATIENT = re.compile(
    r"\bp-\d{3}\b|\bthis (patient|case|person)\b|\bthe (patient|case)\b|\b(his|her|their) "
    r"(history|triggers?|treatment|use|drinking|progress|background|interventions?|symptoms?|age|occupation|living|"
    r"timeline|relapses?|medication|housing|family)\b|\bwhen did\b|\bhow old\b|\bwhat (are|were|is|was) (his|her|their)\b|"
    r"\bwhich interventions? (has|have|did)\b|\b(he|she|him|his|hers?)\b", re.I)

_RESEARCH = re.compile(
    r"\b(research|evidence|stud(y|ies)|guidelines?|recommend\w*|effective\w*|efficacy|supported|trial|trials|"
    r"meta-?analys\w*|review|cochrane|who\b|nida|mhgap|literature|findings?|according to|corpus|documents?|"
    r"what does the)\b", re.I)

# Questions that ask for reasons, mechanisms, effects, or relations are not plain record lookups even when they mention
# the patient: they need research evidence as well.
_REASONING = re.compile(
    r"\b(why|how (does|do|might|could|would|can)|relate[sd]?|relation(ship)?|explain|prevent\w*|effects?|work(s|ed)?|"
    r"helps?|helped|reduc\w+|affect\w*|influenc\w+|contribut\w+|caus\w+|might|could|would|likely|risk|mechanism|alone|enough|sufficient|fail\w*|succeed\w*)\b", re.I)

_PATIENT_IDS = re.compile(r"\bp-\d{3}\b", re.I)
_COMPARE = re.compile(r"\b(compare|compared|comparison|versus|vs\.?|differ(s|ence)?|contrast|similar(ity)?)\b", re.I)


def classify_question(question: str) -> tuple[QuestionClass, str]:
    """Return (class, rule name)."""
    q = question.strip()
    if _INJECTION.search(q):
        return "unsupported", "instruction_override_attempt"
    ids = {i.lower() for i in _PATIENT_IDS.findall(q)}
    if len(ids) >= 2 and _COMPARE.search(q):
        return "unsupported", "multi_patient_comparison_not_supported"
    patient, research, domain = bool(_PATIENT.search(q)), bool(_RESEARCH.search(q)), bool(_DOMAIN.search(q))
    if not (patient or domain):
        return "unsupported", "out_of_scope"
    if patient and research:
        return "comparison", "patient_cue_and_research_cue"
    if patient and _REASONING.search(q):
        return "comparison", "patient_cue_and_reasoning_cue"
    if patient:
        return "patient_history_lookup", "patient_cue_only"
    return "research_question", "research_or_domain_cue"
