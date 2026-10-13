"""routing-lab scenario runner (python -m runner).

Drives the frozen lab stack (mock upstream + litellm-lab proxy) through eight
hard-assertion scenarios, evaluates them against the mock's request journal,
and writes a JSON evidence report to reports/<run_id>.json.
"""
