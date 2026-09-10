import fs from "node:fs";
import path from "node:path";

export default async function globalSetup() {
  const backend = process.env.BLOODNET_BROWSER_BACKEND_URL || "http://127.0.0.1:8080";
  const credentialsPath = process.env.BLOODNET_BROWSER_CREDENTIALS || path.resolve(process.cwd(), "../../.local-test-credentials.json");
  let response: Response;
  try {
    response = await fetch(`${backend}/health`);
  } catch {
    throw new Error(`Browser tests require a running backend at ${backend}.`);
  }
  if (!response.ok) throw new Error(`Browser backend health check failed: ${response.status}`);
  if (!fs.existsSync(credentialsPath)) {
    throw new Error(`Browser role credentials not found at ${credentialsPath}. Run scripts/provision_local_test_accounts.py first.`);
  }
  process.env.BLOODNET_BROWSER_CREDENTIALS = credentialsPath;
}
