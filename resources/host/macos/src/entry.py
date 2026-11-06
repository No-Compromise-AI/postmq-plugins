"""SDK file-path entry; never add a project directory to sys.path."""
import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys
import threading

_LOCK = threading.RLock()


def module(name):
    root = Path(__file__).resolve().parent
    package = "postmq_macos_" + hashlib.sha256(str(root).encode()).hexdigest()
    with _LOCK:
        if package not in sys.modules:
            spec = importlib.util.spec_from_file_location(package, root / "__init__.py",
                submodule_search_locations=[str(root)])
            value = importlib.util.module_from_spec(spec); sys.modules[package] = value
            try: spec.loader.exec_module(value)
            except BaseException:
                sys.modules.pop(package, None); raise
        return importlib.import_module(package + "." + name)


def factory():
    return module("provider").factory()
