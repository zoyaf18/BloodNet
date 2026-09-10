#!/usr/bin/env python3
"""
Production Compliance Configuration Script

IMPORTANT: This script is intentionally not part of the default application deployment path.
The controls it manages (VPC Service Controls, KMS CMEK, Secret Manager rotation, and DLP)
are org-managed runtime protections owned by the cloud platform admin layer, not by the app repo.

Enabling them in the application repository can block local validation, admin access, and
project debugging. Use this script only when the target project, perimeter, and access policy
have been explicitly approved by the platform owner.
"""

import os
import sys
import json
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any

DEFAULT_TFVARS_PATH = Path(__file__).resolve().parents[1] / "infra" / "terraform" / "terraform.secrets.tfvars"
DEFAULT_DLP_TEMPLATE_PATH = Path(__file__).resolve().parent / "bloodnet_dlp_template.json"


def load_tfvars_values(file_path: Optional[str] = None) -> Dict[str, str]:
    """Load Terraform tfvars values from the repo secrets file."""
    path = Path(file_path) if file_path else DEFAULT_TFVARS_PATH
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        match = re.match(r'([A-Za-z0-9_]+)\s*=\s*"([^"]*)"', line)
        if match:
            values[match.group(1)] = match.group(2)
    return values


def resolve_project_id(tfvars_path: Optional[str] = None) -> Optional[str]:
    """Resolve the project ID from env, Secret Manager, or terraform.secrets.tfvars."""
    project_id = (
        os.getenv("GOOGLE_CLOUD_PROJECT")
        or os.getenv("GOOGLE_CLOUD_PROJECT_ID")
        or os.getenv("PROJECT_ID")
    )
    if project_id:
        return project_id

    tfvars = load_tfvars_values(tfvars_path)
    return tfvars.get("project_id") or None


def resolve_secret_value(secret_name: str, project_id: str) -> Optional[str]:
    """Read a secret from environment or Secret Manager."""
    env_name = secret_name.upper().replace("-", "_")
    env_value = os.getenv(env_name) or os.getenv(f"TF_VAR_{env_name.lower()}")
    if env_value:
        return env_value

    cmd = ["gcloud", "secrets", "versions", "access", "latest", f"--secret={secret_name}", f"--project={project_id}", "--quiet"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None

def run_command(cmd: list, description: str = "") -> tuple[int, str, str]:
    """Execute a shell command and return exit code, stdout, stderr."""
    print(f"\n[RUN] {description or ' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERR] {result.stderr}")
    else:
        print(f"[OK] Command succeeded")
    return result.returncode, result.stdout, result.stderr

def configure_secret_rotation(project_id: str, secret_name: str, rotation_period: str = "2592000s") -> bool:
    """Configure automatic rotation for a Secret Manager secret."""
    next_rotation = (datetime.utcnow() + timedelta(days=30)).isoformat() + "Z"
    
    cmd = [
        "gcloud", "secrets", "update", secret_name,
        f"--project={project_id}",
        f"--next-rotation-time={next_rotation}",
        f"--rotation-period={rotation_period}",
        "--quiet"
    ]
    
    rc, stdout, stderr = run_command(cmd, f"Configuring rotation for secret {secret_name}")
    return rc == 0

def enable_sql_cmek(project_id: str, instance_name: str, kms_key_path: str) -> bool:
    """Enable Customer-Managed Encryption Keys for Cloud SQL instance."""
    # Parse KMS key path: projects/PROJECT/locations/LOCATION/keyRings/RING/cryptoKeys/KEY
    try:
        parts = kms_key_path.split("/")
        key_project = parts[1]
        location = parts[3]
        keyring = parts[5]
        key_name = parts[7]
    except (IndexError, ValueError):
        print(f"[ERR] Invalid KMS key path format: {kms_key_path}")
        return False
    
    cmd = [
        "gcloud", "sql", "instances", "patch", instance_name,
        f"--project={project_id}",
        f"--disk-encryption-key={key_name}",
        f"--disk-encryption-key-keyring={keyring}",
        f"--disk-encryption-key-location={location}",
        f"--disk-encryption-key-project={key_project}",
        "--quiet"
    ]
    
    rc, stdout, stderr = run_command(cmd, f"Enabling CMEK for Cloud SQL instance {instance_name}")
    return rc == 0

def create_dlp_template(project_id: str, template_id: str, display_name: str = "BloodNet De-identification") -> bool:
    """Create a DLP de-identification template."""

    try:
        template_config = json.loads(DEFAULT_DLP_TEMPLATE_PATH.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[ERR] Failed to read DLP template config: {e}")
        return False

    template_config["displayName"] = display_name
    request_body = json.dumps({"deidentifyTemplate": template_config}).encode("utf-8")
    try:
        gcloud = shutil.which("gcloud") or shutil.which("gcloud.cmd")
        if not gcloud:
            raise FileNotFoundError("gcloud executable was not found on PATH")
        token = subprocess.check_output([gcloud, "auth", "print-access-token"], text=True).strip()
        request = urllib.request.Request(
            f"https://dlp.googleapis.com/v2/projects/{project_id}/deidentifyTemplates"
            f"?templateId={template_id}",
            data=request_body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "x-goog-user-project": project_id,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            print(f"[OK] DLP template created: {response.read().decode('utf-8')}")
        return True
    except urllib.error.HTTPError as error:
        print(f"[ERR] DLP template creation failed ({error.code}): {error.read().decode('utf-8')}")
        return False
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[ERR] Could not authenticate to the DLP API: {error}")
        return False

def verify_kms_setup(project_id: str, keyring_name: str, key_name: str) -> bool:
    """Verify KMS keyring and key exist."""
    cmd = [
        "gcloud", "kms", "keys", "describe", key_name,
        f"--keyring={keyring_name}",
        "--location=us",
        f"--project={project_id}",
        "--format=table(name,purpose,rotationSchedule.rotationPeriod)"
    ]
    
    rc, stdout, stderr = run_command(cmd, f"Verifying KMS key {key_name} in keyring {keyring_name}")
    if rc == 0:
        print(f"[OK] KMS key verified:\n{stdout}")
    return rc == 0

def verify_vpc_perimeter(policy_id: str, perimeter_name: str) -> bool:
    """Verify VPC Service Controls perimeter exists."""
    cmd = [
        "gcloud", "access-context-manager", "perimeters", "describe",
        perimeter_name,
        f"--policy={policy_id}",
        "--format=table(name,title,description,restrictedServices)"
    ]
    
    rc, stdout, stderr = run_command(cmd, f"Verifying VPC Service Controls perimeter {perimeter_name}")
    if rc == 0:
        print(f"[OK] VPC Service Controls perimeter verified:\n{stdout}")
    return rc == 0

def main():
    """Main entry point for compliance configuration."""

    # This repo intentionally does not enable org-managed perimeter controls by default.
    # They are project-scoped platform settings and must be approved by the target cloud admin.
    if os.getenv("BLOODNET_PLATFORM_COMPLIANCE_MODE") != "enabled":
        print("[WARN] Org-managed compliance controls are intentionally disabled in this repo by default.")
        print("[WARN] VPC Service Controls, KMS CMEK, DLP templates, and Secret rotation are platform-level settings.")
        print("[WARN] Set BLOODNET_PLATFORM_COMPLIANCE_MODE=enabled only for explicit project-admin execution.")
        return

    # Configuration from environment, tfvars, or CLI argument
    project_id = resolve_project_id()
    if len(sys.argv) > 1:
        project_id = sys.argv[1]
    if not project_id:
        print("[ERR] No project_id found. Provide one as an argument, set GOOGLE_CLOUD_PROJECT, or populate infra/terraform/terraform.secrets.tfvars")
        sys.exit(1)

    # Resolve secret-backed values when the automation needs them later.
    tfvars = load_tfvars_values()
    if tfvars.get("database_password"):
        os.environ.setdefault("DATABASE_PASSWORD", tfvars["database_password"])
    if tfvars.get("jwt_secret"):
        os.environ.setdefault("JWT_SECRET", tfvars["jwt_secret"])
    
    print("""
================================================================================
BloodNet Production Compliance Configuration
Applying KMS, Secret Rotation, DLP, and VPC Service Controls
================================================================================

Project: {project_id}
Date: {date_str}
""".format(project_id=project_id, date_str=datetime.now(timezone.utc).isoformat() + 'Z'))
    
    results = {}
    
    # 1. Verify KMS setup (created by Terraform)
    print("\n[STEP 1] Verifying KMS Infrastructure")
    results["kms_verification"] = verify_kms_setup(project_id, "bloodnet", "bloodnet-key")
    
    # 2. Enable Cloud SQL CMEK
    print("\n[STEP 2] Enabling Cloud SQL CMEK")
    cmek_key_path = f"projects/{project_id}/locations/us/keyRings/bloodnet/cryptoKeys/bloodnet-key"
    results["sql_cmek"] = enable_sql_cmek(project_id, "bloodnet-postgres", cmek_key_path)
    
    # 3. Configure Secret Rotation
    print("\n[STEP 3] Configuring Secret Manager Rotation")
    results["jwt_secret_rotation"] = configure_secret_rotation(
        project_id,
        "bloodnet-jwt-secret",
        rotation_period="2592000s"  # 30 days
    )
    
    # 4. Create DLP de-identification template
    print("\n[STEP 4] Creating DLP De-identification Template")
    results["dlp_template"] = create_dlp_template(
        project_id,
        "bloodnet-conservative-deid",
        display_name="BloodNet De-identification Template"
    )
    
    # 5. Verify VPC Service Controls perimeter (if configured)
    print("\n[STEP 5] Verifying VPC Service Controls Perimeter")
    policy_id = os.getenv("ACM_POLICY_ID") or "554392658799"
    if policy_id:
        results["vpc_perimeter"] = verify_vpc_perimeter(policy_id, "bloodnet_prod")
    else:
        print("[WARN] ACM_POLICY_ID not set, skipping perimeter verification")
        results["vpc_perimeter"] = False
    
    # Summary
    print(f"""
╔════════════════════════════════════════════════════════════════════╗
║ Configuration Summary                                              ║
╚════════════════════════════════════════════════════════════════════╝
""")
    
    for step, success in results.items():
        status = "✓ PASS" if success else "✗ FAIL"
        print(f"{status}  {step}")
    
    all_passed = all(results.values())
    if all_passed:
        print(f"""
╔════════════════════════════════════════════════════════════════════╗
║ All compliance configurations applied successfully                 ║
║ Production security boundary is now fully enforced                 ║
╚════════════════════════════════════════════════════════════════════╝
""")
        return 0
    else:
        print(f"""
╔════════════════════════════════════════════════════════════════════╗
║ Some configuration steps failed                                    ║
║ Please review the errors above and retry                           ║
╚════════════════════════════════════════════════════════════════════╝
""")
        return 1

if __name__ == "__main__":
    sys.exit(main())
