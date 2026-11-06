"""File-path entry point for the PostMQ SDK; no sibling imports from sys.path."""

import hashlib
import importlib
import importlib.util
from pathlib import Path
import sys
import threading


_LOCK = threading.Lock()


def _provider(module_name):
    root = Path(__file__).resolve().parent
    name = "postmq_linux_" + hashlib.sha256(str(root).encode()).hexdigest()
    with _LOCK:
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                name, root / "__init__.py", submodule_search_locations=[str(root)])
            package = importlib.util.module_from_spec(spec)
            sys.modules[name] = package
            try:
                spec.loader.exec_module(package)
            except BaseException:
                sys.modules.pop(name, None)
                raise
        module = importlib.import_module(name + "." + module_name)
    return module


def factory():
    return _provider("linux_cgroup").factory()


def rental_factory():
    return _provider("linux_procscan").factory()
