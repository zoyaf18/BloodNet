"""Verify that the deployment is configured for live Identity Platform token verification."""

from __future__ import annotations

import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from contracts.auth import validate_identity_platform_configuration


def main() -> int:
    try:
        config = validate_identity_platform_configuration()
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print("Identity Platform deployment configuration OK")
    print(f"Audience: {config['audience']}")
    print(f"Project ID: {config['project_id'] or '(not set)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
