See [factory workflow policy](factory/WORKFLOW_POLICY.md) for factory-specific shared-workflow requirements.

## Shared files: add your own, don't edit the shared one (owner, 2026-10-21)

Parallel increments collided on files every feature used to edit, which serialised the
factory and caused merge send-backs. So:

- **Smoke checks:** a feature adds its own `scripts/smoke/features/f<NN>_<feature>.py`
  with `before_restart(ctx)` and/or `after_restart(ctx)` (see that package's
  docstring). Do not edit `scripts/smoke/core.py`, `scripts/smoke/run.py` or another
  feature's module unless the change is genuinely shared. When a feature becomes real,
  drop its 404 entry from `features/f90_unbuilt.py` (one line).
- **HTTP features:** a feature adds its own `app/features/f<NN>_<feature>.py` with
  `install(application, get_settings)` for its routes or mounts and, if it runs
  background work (such as an ingest worker), `lifespan(get_settings)`. See that
  package's docstring. Do not edit `app/main.py` to register a feature.
- **Documentation:** describe a feature in its own `docs/<feature>.md`. `README.md` only
  gets a one-line link to it, not a new section.
