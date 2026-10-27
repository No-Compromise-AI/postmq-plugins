#!/usr/bin/env python3
"""
postmq-claim.py — hold a PostMQ resource claim around one job, and speak for a resource
(resource coordination §8.3–§8.4, issues #1397 and #1398).

    postmq-claim.py run --resource <resource_id> [--ttl S] [--wait S] [--provider F:fn] -- <command> [args…]
    postmq-claim.py reconcile --resource <resource_id> [--claim <claim_id>] [--provider F:fn]
    postmq-claim.py serve --kind K --display-name N --provider F:fn [--interval S]
    postmq-claim.py status --resource <resource_id>

This is the command line. Everything it does lives in postmq_claim.py, the adapter SDK installed beside it: its
docstring describes the launch sequence, the provider protocol, the call budgets, the environment it reads and the
exit statuses. The library is loaded by its path rather than from sys.path, so a file in this directory that happens to
share a standard module's name can never shadow one the SDK imports.
"""

import importlib.util
import os
import sys


def _load_sdk():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "postmq_claim.py")
    if not os.path.isfile(path):
        sys.stderr.write("postmq-claim: postmq_claim.py is missing beside this script; reinstall the kit's claim "
                         "component\n")
        sys.exit(3)
    spec = importlib.util.spec_from_file_location("postmq_claim", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["postmq_claim"] = module  # a provider that imports postmq_claim gets this same module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    sys.exit(_load_sdk().main())
