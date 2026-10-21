"""One module per HTTP feature, so increments add a file instead of editing `main.py`.

A module named `f<NN>_<feature>.py` is loaded in `NN` order by `app.main.create_app`
and may define either or both of:

- `install(application: FastAPI, get_settings: Callable[[], Settings]) -> None`: add
  routes, routers or mounts. `get_settings` raises HTTP 503 until storage is ready.
- `lifespan(get_settings: Callable[[], Settings]) -> AbstractAsyncContextManager[None]`:
  background work that runs while the app is up (for example an ingest worker). It is
  entered after start-up begins, so it must wait for `get_settings` to stop raising
  before touching storage, and must stop cleanly when its context exits."""

import importlib
import pkgutil
from types import ModuleType


def feature_modules() -> list[ModuleType]:
    names = sorted(
        info.name
        for info in pkgutil.iter_modules(__path__)
        if info.name.startswith("f")
    )
    return [importlib.import_module(f"{__name__}.{name}") for name in names]
