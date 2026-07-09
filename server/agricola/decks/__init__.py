"""Compendium deck modules. Importing this package registers every
implemented compendium card; UNIMPLEMENTED maps card codes to the
reason they are not (yet) implemented."""

import importlib
import pkgutil

UNIMPLEMENTED = {}

for _mod_info in pkgutil.iter_modules(__path__):
    if _mod_info.name.startswith("deck_"):
        _mod = importlib.import_module(f"{__name__}.{_mod_info.name}")
        UNIMPLEMENTED.update(getattr(_mod, "UNIMPLEMENTED", {}))
