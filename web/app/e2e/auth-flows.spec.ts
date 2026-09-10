import fs from "node:fs";
import path from "node:path";
import { expect, test } from "@playwright/test";

type Role = "hospital_coordinator";
type Credentials = Record<Role, { email: string; password: string }>;

const credentials = JSON.parse(
  fs.readFileSync(
    process.env.BLOODNET_BROWSER_CREDENTIALS || path.resolve(process.cwd(), "../../.local-test-credentials.json"),
    "utf8"
  )
) as Credentials;

async function login(page: import("@playwright/test").Page, role: Role) {
  await page.goto("/");
  await page.getByLabel("Email").fill(credentials[role].email);
  await page.getByLabel("Password").fill(credentials[role].password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("BloodNet / Hospital coordinator")).toBeVisible();
}

test.describe("signup and invitation flows", () => {
  test("donor signup creates an account and requests verification", async ({ page }) => {
    const email = `browser-signup-${Date.now()}@bloodnet.local`;
    await page.goto("/");
    await page.getByRole("button", { name: "Create an account" }).click();
    await expect(page.getByRole("heading", { name: "Create your donor account" })).toBeVisible();
    await page.getByLabel("Full name").fill("Browser Signup Donor");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password", { exact: true }).fill("BrowserSignup!2026");
    await page.getByLabel("Confirm password").fill("BrowserSignup!2026");
    await page.getByRole("button", { name: "Create donor account" }).click();
    await expect(page.getByText("Account created. Check your email to verify your account, then sign in.")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Welcome back" })).toBeVisible();
  });

  test("invitation acceptance creates the invited workspace and invalid tokens are rejected", async ({ page }) => {
    await login(page, "hospital_coordinator");
    const invitation = await page.evaluate(async ({ email }) => {
      const token = sessionStorage.getItem("bloodnet_access_token");
      const profileResponse = await fetch("/match-svc/api/v1/me", { headers: { Authorization: `Bearer ${token}` } });
      const profile = await profileResponse.json();
      const response = await fetch("/match-svc/api/v1/invitations", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ organization_id: profile.organization.id, email, role: "auditor" }),
      });
      if (!response.ok) throw new Error(`${response.status}: ${await response.text()}`);
      return response.json();
    }, { email: `browser-invite-${Date.now()}@bloodnet.local` });

    await page.getByRole("button", { name: "Sign out" }).click();
    await page.goto(`/?invitation=${encodeURIComponent(invitation.token)}`);
    await expect(page.getByRole("heading", { name: "Accept invitation" })).toBeVisible();
    await page.getByLabel("Full name").fill("Browser Invited Auditor");
    await page.getByLabel("Password", { exact: true }).fill("BrowserInvite!2026");
    await page.getByLabel("Confirm password").fill("BrowserInvite!2026");
    await page.getByRole("button", { name: "Accept invitation" }).click();
    await expect(page.getByText("BloodNet / Auditor")).toBeVisible();

    await page.getByRole("button", { name: "Sign out" }).click();
    await page.goto("/?invitation=invalid-browser-token");
    await expect(page.getByRole("heading", { name: "Accept invitation" })).toBeVisible();
    await page.getByLabel("Full name").fill("Invalid Invitation");
    await page.getByLabel("Password", { exact: true }).fill("BrowserInvite!2026");
    await page.getByLabel("Confirm password").fill("BrowserInvite!2026");
    await page.getByRole("button", { name: "Accept invitation" }).click();
    await expect(page.getByText("Invalid invitation token")).toBeVisible();
  });
});