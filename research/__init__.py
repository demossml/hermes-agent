"""
Research Mode — multi-stage deep research for Hermes Agent.

Activation:
    /research <topic>           — explicit
    should_activate_research()  — auto-detect from user message
"""
from research.pipeline import ResearchPipeline, ResearchResult, SubQuestion, should_activate_research

__all__ = ["ResearchPipeline", "ResearchResult", "SubQuestion", "should_activate_research"]
