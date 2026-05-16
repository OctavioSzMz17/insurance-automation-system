"""Core orchestrator components: LLM models, prompts, and tool wrappers.

This package holds provider-agnostic agent infrastructure that is shared
across every agent in the system.

Structure:
    models/   — Chat model factory with multi-provider + fallback support
    prompts/  — Per-agent system prompts
    tools/    — LangChain @tool wrappers around the MCP server functions
"""
