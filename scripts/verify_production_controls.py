"""Verify the production deployment has the required cloud security controls configured."""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from contracts.security_policy import load_edge_security_config


def main() -> int:
    try:
        config = load_edge_security_config()
    except ValueError as exc:
        print(f"Production security controls missing: {exc}", file=sys.stderr)
        return 1

    print("Production security controls OK")
    print(f"Project: {config.project_id or '(not set)'}")
    print(f"Region: {config.region or '(not set)'}")
    print(f"Secret Manager: {config.secret_manager_name or '(not set)'}")
    print(f"Rotation schedule: {config.secret_rotation_schedule or '(not set)'}")
    print(f"CMEK: {config.cmek_key or '(not set)'}")
    print(f"VPC perimeter: {config.vpc_service_perimeter or '(not set)'}")
    print(f"DLP template: {config.dlp_template or '(not set)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
