"""Deploy only the isolated preview artifact; suppress credential-bearing CLI diagnostics."""
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PROJECT = "project-bae56d7f-3ee2-48fc-bdd"


def main():
    config = ROOT / ".logs/mvp-candidate-firebase.json"
    config.write_text(json.dumps({"hosting": {"public": str(ROOT / "web/app/dist-mvp-review"),
        "ignore": ["**/.*", "**/node_modules/**"], "rewrites": [{"source": "**", "destination": "/index.html"}]}}))
    cli = Path(os.environ["APPDATA"]) / "npm/node_modules/firebase-tools/lib/bin/firebase.js"
    environment = dict(os.environ)
    environment.pop("DEBUG", None)
    result = subprocess.run(["node", str(cli), "hosting:channel:deploy", "mvp-review-20260910",
        "--expires", "7d", "--project", PROJECT, "--config", str(config), "--non-interactive"],
        cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    # Firebase can include Identity Platform signing configuration in debug
    # output. Never persist stdout/stderr; sanitize its automatic debug file.
    for path in (ROOT / "firebase-debug.log", ROOT / ".logs/firebase-debug.log"):
        if path.exists():
            pattern = r'("(?:signerKey|saltSeparator|access_token|refresh_token|id_token)"\s*:\s*")[^"]*(")'
            path.write_text(re.sub(pattern, lambda m: m[1] + "[REDACTED]" + m[2], path.read_text(errors="replace")))
    if result.returncode:
        errors = [line for line in (result.stdout + result.stderr).splitlines() if line.strip().startswith("Error:")]
        raise RuntimeError("Preview deployment failed: " + " ".join(errors)[:600])
    urls = re.findall(r"https://[a-z0-9-]+--mvp-review-20260910-[a-z0-9]+\.web\.app", result.stdout)
    report = {"origin": urls[-1] if urls else None, "artifact": "web/app/dist-mvp-review",
              "production_hosting_modified": False, "production_origin": "https://bloodnet-app.web.app"}
    (ROOT / "docs/mvp-candidate-hosting.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
