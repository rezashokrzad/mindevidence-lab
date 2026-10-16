"""The LangGraph workflow.

    START → validate_input → classify → decide_retrieval → [retrieve → assess_evidence]? → generate
          → validate_citations → finalize → END

Conditional edges end the run early (straight to `finalize`) on invalid input, urgent wording, refused or
unsupported requests, retrieval failure, "no evidence" in RAG mode, and model failure. See docs/architecture.md.
"""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph

from app.workflow import nodes
from app.workflow.nodes import WorkflowDeps, traced
from app.workflow.state import WorkflowState

NODE_ORDER = ["validate_input", "classify", "decide_retrieval", "retrieve", "assess_evidence", "generate",
              "validate_citations", "finalize"]


def _ended(state: WorkflowState) -> bool:
    return state.terminal is not None


def build_graph(deps: WorkflowDeps):
    g = StateGraph(WorkflowState)
    fns = {
        "validate_input": nodes.validate_input, "classify": nodes.classify, "decide_retrieval": nodes.decide_retrieval,
        "retrieve": nodes.retrieve, "assess_evidence": nodes.assess_evidence, "generate": nodes.generate,
        "validate_citations": nodes.validate_citations, "finalize": nodes.finalize,
    }
    for name, fn in fns.items():
        g.add_node(name, traced(name, fn, deps))

    g.add_edge(START, "validate_input")
    g.add_conditional_edges("validate_input", lambda s: "finalize" if _ended(s) else "classify",
                            {"finalize": "finalize", "classify": "classify"})
    g.add_conditional_edges("classify", lambda s: "finalize" if _ended(s) else "decide_retrieval",
                            {"finalize": "finalize", "decide_retrieval": "decide_retrieval"})

    def after_decide(s: WorkflowState) -> str:
        d = s.retrieval_decision
        if d and d.needed and not d.deferred_to_model:
            return "retrieve"
        return "assess_evidence"      # records why retrieval is skipped or left to the model's tool

    g.add_conditional_edges("decide_retrieval", after_decide,
                            {"retrieve": "retrieve", "assess_evidence": "assess_evidence"})
    g.add_conditional_edges("retrieve", lambda s: "finalize" if _ended(s) else "assess_evidence",
                            {"finalize": "finalize", "assess_evidence": "assess_evidence"})
    g.add_conditional_edges("assess_evidence", lambda s: "finalize" if _ended(s) else "generate",
                            {"finalize": "finalize", "generate": "generate"})
    g.add_conditional_edges("generate", lambda s: "finalize" if _ended(s) else "validate_citations",
                            {"finalize": "finalize", "validate_citations": "validate_citations"})
    g.add_edge("validate_citations", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def run_workflow(graph, **inputs) -> WorkflowState:
    state = WorkflowState(started_monotonic=time.monotonic(), **inputs)
    final = graph.invoke(state)
    return WorkflowState.model_validate(final)


def mermaid(graph) -> str:
    return graph.get_graph().draw_mermaid()
