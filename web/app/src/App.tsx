import { FormEvent, lazy, Suspense, useEffect, useState } from "react";
import {
  clearAuthToken,
  hasAuthToken,
  setAuthToken,
} from "./authSession";
import type { ConfirmationResult, RecaptchaVerifier } from "firebase/auth";
import { api, identityPlatformEnabled } from "./apiClient";
import {
  INDIAN_CITIES,
  INDIAN_STATES_AND_UNION_TERRITORIES,
} from "./indiaLocations";

export { api } from "./apiClient";

const AppShell = lazy(() =>
  import("./AppShell").then((module) => ({ default: module.AppShell })),
);
const PublicLanding = lazy(() =>
  import("./PublicLanding").then((module) => ({ default: module.PublicLanding })),
);

const loadIdentity = () => import("./identity");
const signInIdentity = async (email: string, password: string) =>
  (await loadIdentity()).signIn(email, password);
const signUpIdentity = async (email: string, password: string, name: string) =>
  (await loadIdentity()).signUp(email, password, name);
const resendVerification = async (email: string, password: string) =>
  (await loadIdentity()).resendVerification(email, password);
const verifyEmailActionCode = async (code: string) =>
  (await loadIdentity()).verifyEmailActionCode(code);
const signOutIdentity = async () => (await loadIdentity()).signOutIdentity();

export type Role =
  | "donor"
  | "hospital_coordinator"
  | "bank_admin"
  | "regional_admin"
  | "auditor";
export type Recommendation = {
  rec_id: string;
  type: string;
  state: string;
  case_id?: string;
  rationale?: string;
  expected_impact?: Record<string, number>;
  payload: {
    unit_ids?: string[];
    bank_id?: string;
    target_units?: number;
    donor_ids?: string[];
  };
};
export type CaseView = {
  case: {
    case_id: string;
    request_id: string;
    fulfillment_probability: number;
    reservation_state: string;
    escalation_state?: string;
    units_from_inventory: number;
    units_from_donors_remaining: number;
    units_from_donors_fulfilled?: number;
    outcome?: string;
  };
  request?: {
    group: string;
    component: string;
    qty: number;
    urgency: string;
    hospital_id: string;
  };
  recommendations?: Recommendation[];
  ranked_donors?: {
    donor_id: string;
    blood_group: string;
    success_probability: number;
  }[];
  swarm?: { status: string; cohort_size?: number; donors_contacted?: string[] };
  notifications?: { status: string }[];
};
export type InventoryUnit = {
  unit_id: string;
  bank_id: string;
  group: string;
  component: string;
  status: string;
  expires_at: string;
};
export type InventoryReservation = {
  reservation_id: string;
  case_id: string;
  request_id: string;
  bank_id: string;
  unit_ids: string[];
  status: string;
  created_at: string;
  expires_at?: string | null;
  consumed_at?: string | null;
  released_at?: string | null;
};
export type Opportunity = {
  outreach_id: string;
  request_id: string;
  message: string;
  status: string;
  created_at: string;
};
export type AuditEvent = {
  audit_id: string;
  action: string;
  actor?: string;
  case_id: string;
  at: string;
};
export type Identity = {
  subject_id: string;
  email?: string;
  display_name?: string;
  role: Role;
  bank_id?: string;
  hospital_id?: string;
  region_id?: string;
  location?: { lat: number; lng: number; accuracy_m?: number | null };
  organization?: { id: string; name: string; type: string };
};
export type ForecastPoint = {
  target_date: string;
  blood_group: string;
  component: string;
  predicted_demand: number;
  lower_bound: number;
  upper_bound: number;
  projected_supply: number;
  shortage_probability: number;
};

const productionIdentityAuth = import.meta.env.PROD && identityPlatformEnabled;
const saveToken = (token: string) => setAuthToken(token);
const clearToken = () => clearAuthToken();

function AuthView({ onAuthenticated }: { onAuthenticated: () => void }) {
  const params = new URLSearchParams(window.location.search);
  const actionCode = params.get("oobCode");
  const [mode, setMode] = useState<
    | "login"
    | "signup"
    | "invitation"
    | "phone"
    | "phone-otp"
    | "verify"
    | "reset"
    | "reset-confirm"
    | "mfa"
  >(
    params.get("invitation")
      ? "invitation"
      : params.get("mode") === "reset" && params.has("token")
        ? "reset-confirm"
        : actionCode
          ? "verify"
          : "login",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfaCode, setMfaCode] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("donor");
  const [phone, setPhone] = useState("");
  const [phoneCode, setPhoneCode] = useState("");
  const [phoneConfirmation, setPhoneConfirmation] =
    useState<ConfirmationResult | null>(null);
  const [phoneVerifier, setPhoneVerifier] = useState<RecaptchaVerifier | null>(
    null,
  );
  const [token, setToken] = useState(params.get("token") || "");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!identityPlatformEnabled || !actionCode) return;
    setBusy(true);
    verifyEmailActionCode(actionCode)
      .then(() => {
        setNotice("Email verified. You can now sign in.");
        setMode("login");
      })
      .catch((reason) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Email verification failed",
        ),
      )
      .finally(() => setBusy(false));
  }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (mode === "login") {
        const accessToken = identityPlatformEnabled
          ? await signInIdentity(email, password)
          : (
              await api<{ access_token: string }>(
                "/match-svc/api/v1/auth/login",
                { method: "POST", body: JSON.stringify({ email, password }) },
              )
            ).access_token;
        saveToken(accessToken);
        try {
          await api("/match-svc/api/v1/auth/me");
          onAuthenticated();
        } catch (reason) {
          if (
            reason instanceof Error &&
            reason.message === "MFA verification required"
          ) {
            setMode("mfa");
            setNotice("Enter the code from your authenticator app.");
          } else throw reason;
        }
      } else if (mode === "signup") {
        if (identityPlatformEnabled) {
          await signUpIdentity(email, password, name);
        } else {
          const result = await api<{ access_token?: string }>(
            "/match-svc/api/v1/auth/signup",
            {
              method: "POST",
              body: JSON.stringify({
                email,
                password,
                password_confirm: confirmation,
                display_name: name,
                role,
              }),
            },
          );
          if (result.access_token) {
            saveToken(result.access_token);
            onAuthenticated();
            return;
          }
        }
        setNotice("Check your email to verify your account.");
        setMode("verify");
      } else if (mode === "verify" && !actionCode) {
        const result = await api<{ access_token?: string; status?: string }>(
          `/match-svc/api/v1/auth/verify-email?token=${encodeURIComponent(token)}`,
          { method: "POST" },
        );
        if (result.access_token) {
          saveToken(result.access_token);
          onAuthenticated();
        } else
          setNotice(
            "Email verified. Your organization approval is still pending.",
          );
      } else if (mode === "reset") {
        await api("/match-svc/api/v1/auth/password-reset", {
          method: "POST",
          body: JSON.stringify({ email }),
        });
        setNotice("If the email exists, a reset link has been sent.");
      } else if (mode === "reset-confirm") {
        await api("/match-svc/api/v1/auth/password-reset-confirm", {
          method: "POST",
          body: JSON.stringify({
            token,
            password,
            password_confirm: confirmation,
          }),
        });
        setNotice(
          "Password reset complete. You can sign in with your new password.",
        );
        setMode("login");
        setPassword("");
        setConfirmation("");
      } else {
        const result = await api<{ access_token: string }>(
          "/match-svc/api/v1/auth/mfa/verify",
          { method: "POST", body: JSON.stringify({ code: mfaCode }) },
        );
        saveToken(result.access_token);
        onAuthenticated();
      }
    } catch (reason) {
      setError(
        reason instanceof Error ? reason.message : "Authentication failed",
      );
    } finally {
      setBusy(false);
    }
  };
  const title =
    mode === "login"
      ? "Welcome back"
      : mode === "signup"
        ? "Create your account"
        : mode === "verify"
          ? "Verify your email"
          : mode === "reset"
            ? "Recover your account"
            : mode === "reset-confirm"
              ? "Choose a new password"
              : "Confirm your identity";
  const staffSso = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/match-svc/api/v1/me");
      onAuthenticated();
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Staff access is unavailable",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <main className="auth-shell">
      <section className="auth-card">
        <p className="eyebrow">BloodNet / secure access</p>
        <h1>{title}</h1>
        <p className="auth-copy">
          Your care network, with identity and access resolved by BloodNet.
        </p>
        {notice && <div className="notice">{notice}</div>}
        {error && <div className="notice error">{error}</div>}
        <form onSubmit={submit}>
          {mode === "signup" && (
            <label>
              Name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </label>
          )}
          {mode === "phone" && (
            <label>
              Mobile number
              <input
                type="tel"
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
                placeholder="+91 99999 99999"
                required
              />
            </label>
          )}
          {mode === "phone-otp" && (
            <label>
              Verification code
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={phoneCode}
                onChange={(event) => setPhoneCode(event.target.value)}
                required
              />
            </label>
          )}
          {mode !== "verify" &&
            mode !== "mfa" &&
            mode !== "reset-confirm" &&
            mode !== "phone" &&
            mode !== "phone-otp" && (
              <label>
                Email
                <input
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  required
                />
              </label>
            )}
          {mode === "verify" && (
            <label>
              Verification token
              <input
                value={token}
                onChange={(event) => setToken(event.target.value)}
                required
              />
            </label>
          )}
          {mode === "mfa" && (
            <label>
              Authenticator code
              <input
                inputMode="numeric"
                autoComplete="one-time-code"
                value={mfaCode}
                onChange={(event) => setMfaCode(event.target.value)}
                required
              />
            </label>
          )}
          {(mode === "login" ||
            mode === "signup" ||
            mode === "reset-confirm") && (
            <label>
              {mode === "reset-confirm" ? "New password" : "Password"}
              <input
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
            </label>
          )}
          {(mode === "signup" || mode === "reset-confirm") && (
            <label>
              Confirm password
              <input
                type="password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                required
              />
            </label>
          )}{" "}
          {mode === "reset" && (
            <p className="form-hint">
              Enter your email and we will send recovery instructions.
            </p>
          )}
          <div id="phone-recaptcha" />
          {mode === "login" && (
            <button
              type="button"
              className="secondary"
              onClick={() => void staffSso()}
              disabled={busy}
            >
              Continue with staff access
            </button>
          )}
          <button type="submit" className="primary" disabled={busy}>
            {busy
              ? "Working..."
              : mode === "login"
                ? "Sign in"
                : mode === "signup"
                  ? "Create account"
                  : mode === "phone"
                    ? "Send code"
                    : mode === "phone-otp"
                      ? "Verify phone"
                      : mode === "verify"
                        ? "Verify email"
                        : mode === "mfa"
                          ? "Verify code"
                          : mode === "reset-confirm"
                            ? "Reset password"
                            : "Send reset link"}
          </button>
        </form>
        <div className="auth-links">
          {mode !== "login" && mode !== "mfa" && mode !== "reset-confirm" && (
            <button type="button" onClick={() => setMode("login")}>
              Sign in
            </button>
          )}
          {mode !== "signup" && mode !== "mfa" && mode !== "reset-confirm" && (
            <button type="button" onClick={() => setMode("signup")}>
              Sign up
            </button>
          )}
          {mode === "login" && identityPlatformEnabled && (
            <button type="button" onClick={() => setMode("phone")}>
              Donor sign in with phone
            </button>
          )}
          {mode !== "reset" && mode === "login" && (
            <button type="button" onClick={() => setMode("reset")}>
              Forgot password?
            </button>
          )}
          {mode === "signup" && (
            <button type="button" onClick={() => setMode("verify")}>
              I already verified my email
            </button>
          )}
        </div>
      </section>
    </main>
  );
}
function RoleAuthView({ onAuthenticated, embedded = false }: { onAuthenticated: () => void; embedded?: boolean }) {
  const [mode, setMode] = useState<"login" | "signup">(
    new URLSearchParams(window.location.search).has("invitation")
      ? "signup"
      : "login",
  );
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("donor");
  const [organizationName, setOrganizationName] = useState("");
  const [organizationId, setOrganizationId] = useState("");
  const [organizationAddress, setOrganizationAddress] = useState("");
  const [city, setCity] = useState("");
  const [stateName, setStateName] = useState("");
  const [regionId, setRegionId] = useState("");
  const [storageCapacity, setStorageCapacity] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [resending, setResending] = useState(false);
  // Do not advertise resending until we know this session is for an
  // unverified account. This avoids offering an email action to new visitors.
  const [verificationEmailAvailable, setVerificationEmailAvailable] =
    useState(false);
  const invitationMode = new URLSearchParams(window.location.search).has(
    "invitation",
  );

  useEffect(() => {
    const startRegistration = (event: Event) => {
      const requestedRole = (event as CustomEvent<Role>).detail;
      if (!["donor", "hospital_coordinator", "bank_admin"].includes(requestedRole)) return;
      setRole(requestedRole);
      setMode("signup");
    };
    window.addEventListener("bloodnet:register", startRegistration);
    return () => window.removeEventListener("bloodnet:register", startRegistration);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const actionCode = params.get("oobCode");
    const verificationToken = params.get("token");
    if (productionIdentityAuth && actionCode) {
      setBusy(true);
      verifyEmailActionCode(actionCode)
        .then(() => {
          setNotice("Email verified. You can now sign in.");
          window.history.replaceState({}, "", window.location.pathname);
        })
        .catch((reason) =>
          setError(
            reason instanceof Error
              ? reason.message
              : "Email verification failed",
          ),
        )
        .finally(() => setBusy(false));
      return;
    }
    if (params.get("mode") !== "verify" || !verificationToken) return;
    setBusy(true);
    api<{ access_token?: string }>(
      `/match-svc/api/v1/auth/verify-email?token=${encodeURIComponent(verificationToken)}`,
      { method: "POST" },
    )
      .then((result) => {
        if (result.access_token) {
          saveToken(result.access_token);
          onAuthenticated();
        } else {
          setNotice("Email verified. You can now sign in.");
          window.history.replaceState({}, "", window.location.pathname);
        }
      })
      .catch((reason) =>
        setError(
          reason instanceof Error
            ? reason.message
            : "Email verification failed",
        ),
      )
      .finally(() => setBusy(false));
  }, [onAuthenticated]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    setNotice("");
    try {
      if (invitationMode) {
        const invitation = new URLSearchParams(window.location.search).get(
          "invitation",
        );
        if (!invitation) throw new Error("Invitation token is missing.");
        const result = await api<{ access_token: string }>(
          `/match-svc/api/v1/invitations/${encodeURIComponent(invitation)}/accept`,
          {
            method: "POST",
            body: JSON.stringify({
              token: invitation,
              password,
              password_confirm: confirmation,
              display_name: name,
            }),
          },
        );
        saveToken(result.access_token);
        onAuthenticated();
      } else if (mode === "signup") {
        if (role === "hospital_coordinator" || role === "bank_admin") {
          // In production Identity Platform mode, the backend onboarding flow needs
          // a matching Firebase account first; otherwise the approved bank/hospital
          // user cannot complete Firebase sign-in even though the organization
          // membership was approved.
          if (productionIdentityAuth) {
            await signUpIdentity(email, password, name);
          }
          await api("/match-svc/api/v1/organizations/onboard", {
            method: "POST",
            body: JSON.stringify({
              name: organizationName,
              type: role === "hospital_coordinator" ? "hospital" : "blood_bank",
              contact_email: email,
              address: [organizationAddress, city, stateName]
                .filter(Boolean)
                .join(", "),
              metadata:
                role === "bank_admin"
                  ? {
                      storage_capacity: storageCapacity,
                      registration_id: organizationId,
                      region_id: regionId
                        .trim()
                        .toLowerCase()
                        .replaceAll(/\s+/g, "-"),
                    }
                  : {
                      registration_id: organizationId,
                      region_id: regionId
                        .trim()
                        .toLowerCase()
                        .replaceAll(/\s+/g, "-"),
                    },
              owner_email: email,
              owner_name: name,
              owner_password: password,
              owner_password_confirm: confirmation,
            }),
          });
        } else if (productionIdentityAuth)
          await signUpIdentity(email, password, name);
        else
          await api("/match-svc/api/v1/auth/signup", {
            method: "POST",
            body: JSON.stringify({
              email,
              password,
              password_confirm: confirmation,
              display_name: name,
            }),
          });
        setNotice(
          "Account created. Check your email to verify your account, then sign in.",
        );
        setVerificationEmailAvailable(true);
        setMode("login");
      } else {
        const accessToken = productionIdentityAuth
          ? await signInIdentity(email, password)
          : (
              await api<{ access_token: string }>(
                "/match-svc/api/v1/auth/login",
                { method: "POST", body: JSON.stringify({ email, password }) },
              )
            ).access_token;
        saveToken(accessToken);
        onAuthenticated();
      }
    } catch (reason) {
      const message =
        reason instanceof Error ? reason.message : "Authentication failed";
      // A valid login for an unverified account is the other permitted point
      // at which the resend action becomes available.
      if (
        mode === "login" &&
        /(?:email.*(?:not verified|pending verification)|verify your email|verification link)/i.test(
          message,
        )
      ) {
        setVerificationEmailAvailable(true);
      }
      setError(message);
    } finally {
      setBusy(false);
    }
  };
  const resend = async () => {
    setResending(true);
    setError("");
    try {
      await resendVerification(email, password);
      setNotice(
        "A new verification email has been sent. Check Inbox and Spam.",
      );
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "Unable to resend verification email",
      );
    } finally {
      setResending(false);
    }
  };
  const Root = embedded ? "div" : "main";
  return (
    <Root className={embedded ? "auth-shell embedded" : "auth-shell"}>
      <section className="auth-card">
        <p className="eyebrow">BloodNet / secure access</p>
        <h1>
          {invitationMode
            ? "Accept invitation"
            : mode === "login"
              ? "Welcome back"
              : `Create ${role === "donor" ? "your donor" : role === "hospital_coordinator" ? "your hospital" : "your blood bank"} account`}
        </h1>
        <p className="auth-copy">
          {mode === "login"
            ? "Sign in, or create an account as a donor, hospital, or blood bank."
            : "Join BloodNet as a donor, hospital, or blood bank."}
        </p>
        {notice && <div className="notice">{notice}</div>}
        {error && <div className="notice error">{error}</div>}
        <form onSubmit={submit}>
          {mode === "signup" && !invitationMode && (
            <>
              <label>
                Register as
                <select
                  value={role}
                  onChange={(event) => setRole(event.target.value as Role)}
                >
                  <option value="donor">Donor</option>
                  <option value="hospital_coordinator">Hospital</option>
                  <option value="bank_admin">Blood bank</option>
                </select>
              </label>
              {(role === "hospital_coordinator" || role === "bank_admin") && (
                <>
                  <label>
                    {role === "hospital_coordinator"
                      ? "Hospital name"
                      : "Blood bank name"}
                    <input
                      value={organizationName}
                      onChange={(event) =>
                        setOrganizationName(event.target.value)
                      }
                      required
                    />
                  </label>
                  <label>
                    {role === "hospital_coordinator"
                      ? "Hospital registration / ID"
                      : "Licence / registration ID"}
                    <input
                      value={organizationId}
                      onChange={(event) =>
                        setOrganizationId(event.target.value)
                      }
                      required
                    />
                  </label>
                  <label>
                    Address
                    <input
                      value={organizationAddress}
                      onChange={(event) =>
                        setOrganizationAddress(event.target.value)
                      }
                      required
                    />
                  </label>
                  <label>
                    City
                    <select
                      value={city}
                      onChange={(event) => setCity(event.target.value)}
                      required
                    >
                      <option value="">Select an Indian city</option>
                      {INDIAN_CITIES.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    State
                    <select
                      value={stateName}
                      onChange={(event) => setStateName(event.target.value)}
                      required
                    >
                      <option value="">Select a state or union territory</option>
                      {INDIAN_STATES_AND_UNION_TERRITORIES.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Operational region
                    <select value={regionId} onChange={(event) => setRegionId(event.target.value)} required>
                      <option value="">Select an operational city</option>
                      {INDIAN_CITIES.map((option) => (
                        <option key={option} value={option}>{option}</option>
                      ))}
                    </select>
                  </label>
                  {role === "bank_admin" && (
                    <label>
                      Storage capacity
                      <input
                        value={storageCapacity}
                        onChange={(event) =>
                          setStorageCapacity(event.target.value)
                        }
                      />
                    </label>
                  )}
                </>
              )}
            </>
          )}
          {!invitationMode && (
            <label>
              Email
              <input
                type="email"
                value={email}
                onChange={(event) => {
                  setEmail(event.target.value);
                  setVerificationEmailAvailable(false);
                }}
                required
              />
            </label>
          )}
          {(invitationMode || mode === "signup") && (
            <label>
              Full name
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </label>
          )}
          <label>
            Password
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
            />
          </label>
          {(invitationMode || mode === "signup") && (
            <label>
              Confirm password
              <input
                type="password"
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                required
              />
            </label>
          )}
          <button
            type="submit"
            className="primary"
            disabled={busy}
          >
            {busy
              ? "Working..."
              : invitationMode
                ? "Accept invitation"
                : mode === "login"
                  ? "Sign in"
                  : `Create ${role === "donor" ? "donor" : role === "hospital_coordinator" ? "hospital" : "blood bank"} account`}
          </button>
        </form>
        <div className="auth-links">
          {!invitationMode && (
            <button
              type="button"
              onClick={() => setMode(mode === "login" ? "signup" : "login")}
            >
              {mode === "login" ? "Create an account" : "Back to sign in"}
            </button>
          )}
          {mode === "login" &&
            productionIdentityAuth &&
            verificationEmailAvailable && (
            <button
              type="button"
              onClick={() => void resend()}
              disabled={resending}
            >
              {resending ? "Sending..." : "Resend verification email"}
            </button>
          )}
        </div>
        {mode === "signup" && !invitationMode && (
          <p className="form-hint">
            Donors can join individually. Hospitals and licensed blood banks
            can register their organization and operational region here.
          </p>
        )}
      </section>
    </Root>
  );
}

export function App() {
  const [authenticated, setAuthenticated] = useState(hasAuthToken());

  const handleSignOut = async () => {
    clearToken();
    if (identityPlatformEnabled) await signOutIdentity();
    setAuthenticated(false);
  };

  if (!authenticated) {
    const params = new URLSearchParams(window.location.search);
    const authAction = params.has("invitation") || params.has("oobCode") || params.get("mode") === "verify";
    if (authAction) return <RoleAuthView onAuthenticated={() => setAuthenticated(true)} />;
    return (
      <Suspense fallback={<main className="auth-shell"><p>Loading BloodNet…</p></main>}>
        <PublicLanding auth={<RoleAuthView embedded onAuthenticated={() => setAuthenticated(true)} />} />
      </Suspense>
    );
  }
  return (
    <Suspense fallback={<main className="auth-shell"><p>Loading workspace…</p></main>}>
      {import.meta.env.VITE_BLOODNET_VALIDATION_MODE === "true" && (
        <div role="status" style={{ padding: "10px 20px", background: "#fff3cd", color: "#664d03", textAlign: "center" }}>
          Validation workspace: simulated blood stock and receipts. No real donation or attendance is requested.
        </div>
      )}
      <AppShell onSignOut={handleSignOut} />
    </Suspense>
  );
}
