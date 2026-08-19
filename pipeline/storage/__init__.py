"""Caller-side persistence helpers. Not LangGraph nodes -- graph.py's
covered_events-update node is explicitly out of scope (see
pipeline/__init__.py); this module only gives a caller (e.g. pipeline/run.py)
somewhere to load/save CoveredEvent state between weekly runs.
"""
