"""One module per feature's smoke checks, so increments never edit each other's files.

A module named `f<NN>_<feature>.py` runs in `NN` order and defines either or both of:

- `before_restart(ctx: Context) -> None`: run against the first app start, after the
  core has checked health, identity and the built page. Register sources with
  `core.register_handbook` and append them to `ctx.sources`; keep values needed after
  the restart in `ctx.data`.
- `after_restart(ctx: Context) -> None`: run against the restarted app on the same
  database and data directory; prove what the feature persisted survived.

Record evidence in `ctx.observations` under the feature's own keys. A new feature
adds its own module here; it does not edit `core.py`, `run.py` or another feature's
module."""
