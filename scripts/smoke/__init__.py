"""End-to-end smoke: `python -m scripts.smoke [--output result.json]`.

`core.py` owns the app process and shared helpers, `run.py` the single run (first
start, features, restart, missing database), and `features/` one module per feature,
so increments that add checks never edit the same file."""
