"""Production-probe toolkit for the LiteLLM router: ``python -m prober``.

One probe per run, rotating through every model the router serves, with a
JSONL history, a daily budget guard and an offline ``--dry-run``/``--status``
mode. See ``prober/README.md``.
"""
