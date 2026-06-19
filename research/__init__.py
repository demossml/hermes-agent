"""
Research Mode — multi-stage deep research for Hermes Agent.

Activation:
    /research <topic>           — explicit
    should_activate_research()  — auto-detect from user message
"""
from research.pipeline import (
    ResearchPipeline, ResearchResult, SubQuestion,
    should_activate_research, decompose_research_query,
)
from research.agents import (
    SearcherAgent, ReaderAgent, SearchResult, Claim,
    SearchReport, run_search_and_read,
)
from research.cross_validator import (
    CrossValidator, Adjudicator, ValidatedClaim,
    ValidationReport, run_cross_validation,
)
from research.synthesizer import Synthesizer

__all__ = [
    "ResearchPipeline", "ResearchResult", "SubQuestion",
    "should_activate_research", "decompose_research_query",
    "SearcherAgent", "ReaderAgent", "SearchResult", "Claim",
    "SearchReport", "run_search_and_read",
    "CrossValidator", "Adjudicator", "ValidatedClaim",
    "ValidationReport", "run_cross_validation",
    "Synthesizer",
]
