import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  AdminFeaturePanels,
  AuditInvestigator,
  DonorOnboarding,
  DonorPortal,
  FacilityLocationEditor,
  HospitalJourneyEvidence,
  HospitalFeaturePanels,
} from "./FeaturePanels";
import {
  AuditSurfaceEnhanced,
  BankSurfaceEnhanced,
  RegionalSurfaceEnhanced,
} from "./SurfacesEnhanced";
import { INDIAN_CITIES } from "./indiaLocations";
import { api, ApiError, userFacingError } from "./apiClient";
import type { Identity, Role } from "./App";
import { realtimeAdapter } from "./realtime";
import type { DecisionResult, Recommendation } from "./RecommendationUI";
import { CardInfo } from "./CardInfo";
import { usePagination } from "./Pagination";
import {
  forecastBloodGroups,
  forecastCellRisk,
  forecastDates as selectForecastDates,
  summarizeForecast,
} from "./forecastPresentation";

type View =
  | "overview"
  | "admin"
  | "donor"
  | "hospital"
  | "org"
  | "account"
  | "privacy"
  | "audit";
type AppApi = <T>(path: string, options?: RequestInit) => Promise<T>;
type UpdateItem = { label: string; detail: string };
type CaseRecord = {
  case: {
    case_id: string;
    request_id: string;
    fulfillment_probability: number;
    reservation_state: string;
    units_from_inventory: number;
    units_from_donors_remaining: number;
    units_from_donors_fulfilled?: number;
    outcome?: string;
    escalation_state?: string;
  };
  request?: {
    group?: string;
    component?: string;
    qty?: number;
    hospital_id?: string;
    urgency?: string;
    source_channel?: string;
  };
  request_ingress?: { provider: string; provider_event_id: string; status: string; received_at: string; processed_at?: string };
  delivery_summary?: { total: number; delivered: number; failed: number; statuses: Record<string, number> };
  supply_resilience?: { supplier_count: number; stocked_supplier_count: number; single_supplier_dependency: boolean; alternative_suppliers: number };
  ranked_donors?: {
    donor_id: string;
    blood_group: string;
    success_probability: number;
    distance_to_bank_km?: number;
    travel_time_min?: number;
    eligibility?: string;
    explanation?: string;
  }[];
  donor_summary?: {
    rank: number;
    blood_group?: string;
    success_probability?: number;
  }[];
  activity?: {
    action: string;
    actor?: string;
    at: string;
    details?: Record<string, unknown>;
  }[];
};

function isCaseActive(item: CaseRecord) {
  return !["fulfilled", "cancelled", "unfulfilled"].includes(
    item.case.outcome || "",
  );
}

const roleNames: Record<Role, string> = {
  hospital_coordinator: "Hospital coordinator",
  bank_admin: "Bank operations",
  donor: "Donor",
  regional_admin: "Regional admin",
  auditor: "Auditor",
};

function initialsForIdentity(identity: Identity) {
  const label =
    identity.display_name?.trim() ||
    identity.email?.split("@")[0] ||
    roleNames[identity.role];
  const parts = label.split(/\s+/).filter(Boolean);
  return (
    parts.length > 1
      ? `${parts[0][0]}${parts[parts.length - 1][0]}`
      : label.slice(0, 2)
  ).toUpperCase();
}

function organizationLabel(identity: Identity) {
  if (identity.role === "donor") {
    return identity.display_name?.trim() || identity.email?.split("@")[0] || "Donor";
  }
  return (
    identity.organization?.name?.replace(/\s+Synthetic\b/gi, "") ||
    "BloodNet network"
  );
}

function UpdatesCenter({
  identity,
  cases,
  recommendations,
  pendingRoleRequestCount,
  activeSwarmCount,
  outreachNotificationCount,
  initials,
  onOpen,
}: {
  identity: Identity;
  cases: CaseRecord[];
  recommendations: Recommendation[];
  pendingRoleRequestCount: number;
  activeSwarmCount: number;
  outreachNotificationCount: number;
  initials: string;
  onOpen: () => void;
}) {
  const [open, setOpen] = useState(false);
  const updates: UpdateItem[] = [];
  const activeCaseCount = cases.filter(isCaseActive).length;
  if (activeCaseCount) {
    updates.push({
      label: `${activeCaseCount} active blood request${activeCaseCount === 1 ? "" : "s"}`,
      detail: "Matching and fulfillment activity is available in your workspace.",
    });
  }
  if (["bank_admin", "regional_admin", "auditor"].includes(identity.role) && recommendations.length) {
    updates.push({
      label: `${recommendations.length} approval update${recommendations.length === 1 ? "" : "s"}`,
      detail: "Recommendations are waiting for review.",
    });
  }
  if (identity.role === "regional_admin" && pendingRoleRequestCount) {
    updates.push({
      label: `${pendingRoleRequestCount} access request${pendingRoleRequestCount === 1 ? "" : "s"}`,
      detail: "A user is waiting for an operational role decision.",
    });
  }
  if (identity.role === "regional_admin" && activeSwarmCount) {
    updates.push({
      label: `${activeSwarmCount} active donor swarm${activeSwarmCount === 1 ? "" : "s"}`,
      detail: "Donor mobilization activity is running in your region.",
    });
  }
  if (identity.role === "donor" && outreachNotificationCount) {
    updates.push({
      label: `${outreachNotificationCount} donor update${outreachNotificationCount === 1 ? "" : "s"}`,
      detail: "Your outreach and donation activity is available to review.",
    });
  }
  const count = updates.length;
  return (
    <div className="updates-center" onMouseLeave={() => setOpen(false)}>
      <button
        className={`avatar updates-trigger${count ? " has-updates" : ""}`}
        onMouseEnter={() => setOpen(true)}
        onClick={() => setOpen((current) => !current)}
        aria-expanded={open}
        aria-label={`${initials} identity and ${count} workspace update${count === 1 ? "" : "s"}`}
      >
        {initials}
        {count > 0 && <b aria-hidden="true">{count}</b>}
      </button>
      {open && (
        <div className="updates-popover" role="status">
          <strong>{count ? "Workspace updates" : "No new updates"}</strong>
          {updates.map((item) => (
            <div className="updates-item" key={item.label}>
              <strong>{item.label}</strong>
              <span>{item.detail}</span>
            </div>
          ))}
          {count > 0 && (
            <button className="primary compact" onClick={() => { setOpen(false); onOpen(); }}>
              Review updates
            </button>
          )}
        </div>
      )}
    </div>
  );
}

const viewsByRole: Record<Role, View[]> = {
  donor: ["overview", "donor", "account", "privacy"],
  hospital_coordinator: ["overview", "hospital", "account", "privacy"],
  bank_admin: ["overview", "admin", "account", "privacy"],
  regional_admin: [
    "overview",
    "admin",
    "audit",
    "org",
    "account",
    "privacy",
  ],
  auditor: ["overview", "admin", "audit", "account", "privacy"],
};

export function AppShell({ onSignOut }: { onSignOut: () => Promise<void> }) {
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [cases, setCases] = useState<CaseRecord[]>([]);
  const [recommendations, setRecommendations] = useState<Recommendation[]>([]);
  const [view, setView] = useState<View>("overview");
  const [regionalAdminTab, setRegionalAdminTab] = useState<
    "weather" | "recommendations"
  >("weather");
  const [pendingRoleRequestCount, setPendingRoleRequestCount] = useState(0);
  const [activeSwarmCount, setActiveSwarmCount] = useState(0);
  const [outreachNotificationCount, setOutreachNotificationCount] = useState(0);
  const [notice, setNotice] = useState("");
  const [caseUpdateErrors, setCaseUpdateErrors] = useState<string[]>([]);
  const [identityState, setIdentityState] = useState<
    "loading" | "ready" | "unavailable" | "authentication_required" | "access_denied"
  >("loading");
  const [identityError, setIdentityError] = useState("");

  const selectView = (nextView: View) => {
    window.history.pushState(
      {},
      "",
      nextView === "overview" ? "/app" : `/app/${nextView}`,
    );
    setView(nextView);
  };

  useEffect(() => {
    const route = window.location.pathname
      .split("/")
      .filter(Boolean)
      .pop() as View;
    if (
      [
        "overview",
        "admin",
        "donor",
        "hospital",
        "org",
        "account",
        "privacy",
        "audit",
      ].includes(route)
    )
      setView(route);
    const onPopState = () =>
      setView(
        (window.location.pathname.split("/").filter(Boolean).pop() as View) ||
          "overview",
      );
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const loadIdentity = async () => {
    setIdentityState("loading");
    setNotice("");
    setIdentityError("");
    try {
      const nextIdentity = await api<Identity>("/match-svc/api/v1/me");
      if (nextIdentity.role === "donor") {
        const donorProfile = await api<{ display_name?: string }>(
          "/match-svc/api/v1/auth/donor-profile",
        ).catch(() => null);
        if (donorProfile?.display_name?.trim()) {
          nextIdentity.display_name = donorProfile.display_name.trim();
        }
      }
      setIdentity(nextIdentity);
      setIdentityState("ready");
    } catch (error) {
      setIdentity(null);
      setIdentityError(userFacingError(error, "We couldn't load your workspace."));
      setIdentityState(
        error instanceof ApiError && error.status === 401
          ? "authentication_required"
          : error instanceof ApiError && error.status === 403
            ? "access_denied"
            : "unavailable",
      );
    }
  };

  useEffect(() => {
    void loadIdentity();
  }, []);

  useEffect(() => {
    if (!identity) return;
    void api<{ cases?: CaseRecord[] }>("/match-svc/api/v1/cases")
      .then((result) => setCases(result.cases || []))
      .catch(() => setCases([]));

    if (["bank_admin", "regional_admin", "auditor"].includes(identity.role)) {
      void api<{ recommendations?: Recommendation[] }>(
        "/match-svc/api/v1/recommendations?state=AWAITING_APPROVAL",
      )
        .then((result) => setRecommendations(result.recommendations || []))
        .catch(() => setRecommendations([]));
    }

    const refreshRegionalAdminCounts = async () => {
      if (identity.role !== "regional_admin") return;

      try {
        const requests = await api<any[]>("/match-svc/api/v1/auth/role-requests");
        setPendingRoleRequestCount(
          requests.filter((item) => item.status === "pending").length,
        );
      } catch {
        setPendingRoleRequestCount(0);
      }

      try {
        const result = await api<{ swarms?: unknown[] }>("/match-svc/api/v1/regional/swarms");
        setActiveSwarmCount(result.swarms?.length || 0);
      } catch {
        setActiveSwarmCount(0);
      }
    };

    if (identity.role === "regional_admin") {
      void refreshRegionalAdminCounts();
      const intervalId = window.setInterval(() => {
        void refreshRegionalAdminCounts();
      }, 15000);

      return () => window.clearInterval(intervalId);
    }

    setPendingRoleRequestCount(0);
    setActiveSwarmCount(0);

    if (identity.role === "donor") {
      void api<{ notifications?: unknown[] }>("/match-svc/api/v1/notifications")
        .then((result) => setOutreachNotificationCount(result.notifications?.length || 0))
        .catch(() => setOutreachNotificationCount(0));
    } else {
      setOutreachNotificationCount(0);
    }
  }, [identity]);

  const shareLocation = () => {
    if (!navigator.geolocation) {
      setNotice(
        "This browser cannot share a location. Use a supported browser to take part in proximity matching.",
      );
      return;
    }
    navigator.geolocation.getCurrentPosition(
      ({ coords }) => {
        void api("/match-svc/api/v1/auth/location", {
          method: "PUT",
          body: JSON.stringify({
            lat: coords.latitude,
            lng: coords.longitude,
            accuracy_m: coords.accuracy,
          }),
        })
          .then(() => {
            setIdentity((current) =>
              current
                ? {
                    ...current,
                    location: {
                      lat: coords.latitude,
                      lng: coords.longitude,
                      accuracy_m: coords.accuracy,
                    },
                  }
                : current,
            );
            setNotice("");
          })
          .catch((error) =>
            setNotice(
              error instanceof Error
                ? error.message
                : "Could not save your location.",
            ),
          );
      },
      () =>
        setNotice(
          "Location was not shared. Proximity-based matching will not include your location until you allow it.",
        ),
      { enableHighAccuracy: true, maximumAge: 300000, timeout: 10000 },
    );
  };

  useEffect(() => {
    if (identity && !identity.location) shareLocation();
  }, [identity]);

  useEffect(() => {
    if (!identity || viewsByRole[identity.role].includes(view)) return;
    window.history.replaceState({}, "", "/app");
    setView("overview");
  }, [identity, view]);

  useEffect(() => {
    setCaseUpdateErrors([]);
    if (!identity || !["overview", "hospital"].includes(view) || !cases.length)
      return;
    const subscribedCases = cases.slice(0, 3);
    const unsubscribers = subscribedCases.map((item) =>
      realtimeAdapter.subscribeToCase(item.case.case_id, (snapshot) => {
        setCaseUpdateErrors((current) => current.includes(item.case.case_id) ? current.filter((id) => id !== item.case.case_id) : current);
        setCases((current) =>
          current.map((candidate) =>
            candidate.case.case_id === item.case.case_id
              ? (snapshot as CaseRecord)
              : candidate,
          ),
        );
      }, () => setCaseUpdateErrors((current) => current.includes(item.case.case_id) ? current : [...current, item.case.case_id])),
    );
    return () => unsubscribers.forEach((unsubscribe) => unsubscribe());
  }, [identity, view, cases.map((item) => item.case.case_id).join(",")]);

  useEffect(() => {
    if (!identity || !["bank_admin", "regional_admin", "auditor"].includes(identity.role)) return;
    const refreshRecommendations = () => {
      void api<{ recommendations?: Recommendation[] }>(
        "/match-svc/api/v1/recommendations?state=AWAITING_APPROVAL",
      ).then((result) => setRecommendations(result.recommendations || []));
    };
    window.addEventListener("bloodnet:recommendations-changed", refreshRecommendations);
    return () => window.removeEventListener("bloodnet:recommendations-changed", refreshRecommendations);
  }, [identity]);

  if (!identity) {
    const authenticationRequired = identityState === "authentication_required";
    const unavailable = identityState === "unavailable";
    const accessDenied = identityState === "access_denied";
    return (
      <main className="auth-shell">
        <section className="auth-card">
          <p className="eyebrow">
            {unavailable ? "BloodNet / service status" : "BloodNet / secure access"}
          </p>
          <h1>
            {unavailable
              ? "Workspace temporarily unavailable"
              : authenticationRequired
                ? "Your session has expired"
                : accessDenied
                  ? "Access could not be confirmed"
                : "Loading workspace"}
          </h1>
          {identityError ? (
            <p className="auth-copy">{identityError}</p>
          ) : unavailable || accessDenied ? (
            <p className="auth-copy">
              {unavailable
                ? "We could not reach the workspace service. Your signed-in session is still active. Please try again."
                : "Your account does not currently have access to this workspace."}
            </p>
          ) : (
            <div className="workspace-loader" role="status" aria-label="Loading workspace">
              <span className="workspace-loader-mark" aria-hidden="true" />
              <span>Preparing your workspace</span>
            </div>
          )}
          {(unavailable || authenticationRequired || accessDenied) && (
            <div className="auth-links">
              <button
                className="primary"
                onClick={() => void loadIdentity()}
              >
                Retry
              </button>
              {(authenticationRequired || accessDenied) && (
                <>
                  <button className="secondary" onClick={() => void onSignOut()}>
                    Back to sign in
                  </button>
                  <button
                    type="button"
                    onClick={() =>
                      void onSignOut().then(() =>
                        window.location.assign("/?mode=signup"),
                      )
                    }
                  >
                    Create an account
                  </button>
                </>
              )}
            </div>
          )}
        </section>
      </main>
    );
  }

  const bankAdmin = identity.role === "bank_admin";
  const auditor = identity.role === "auditor";
  const regionalAdmin = identity.role === "regional_admin";
  const homeLabel =
    identity.role === "regional_admin"
      ? "Network health"
      : identity.role === "hospital_coordinator"
        ? "Hospital portal"
        : identity.role === "bank_admin"
          ? "Inventory"
          : identity.role === "donor"
            ? "Donor home"
            : "Audit overview";

  const title =
    view === "admin" && regionalAdmin
      ? regionalAdminTab === "weather"
        ? "Blood Weather"
        : "Recommendations"
      : view === "admin"
      ? "Admin console"
      : view === "donor"
        ? "Donor PWA"
        : view === "hospital"
          ? "Hospital portal"
          : view === "audit"
              ? "Audit investigation"
              : view === "org"
                ? "Organization management"
                : view === "account"
                  ? "Account & session"
                  : view === "privacy"
                    ? "Data minimization & PII policy"
                    : view === "overview"
                      ? homeLabel
                      : `${roleNames[identity.role]} workspace`;

  const decide = async (
    id: string,
    decision: "approve" | "reject",
    rationale?: string,
  ): Promise<DecisionResult> => {
    try {
      const result = await api<DecisionResult>(
        `/match-svc/api/v1/recommendations/${id}/${decision}`,
        {
          method: "POST",
          body: JSON.stringify({
            rationale: rationale || `Reviewed by ${identity.subject_id}`,
          }),
        },
      );
      setRecommendations((current) =>
        current.filter((item) => item.rec_id !== id),
      );
      setNotice(`Recommendation ${decision}d.`);
      return result;
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "Decision failed");
      throw error;
    }
  };

  const refreshCases = async () => {
    const result = await api<{ cases?: CaseRecord[] }>(
      "/match-svc/api/v1/cases",
    );
    setCases(result.cases || []);
  };

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            +
          </span>
          <strong>BloodNet</strong>
        </div>
        <div className="network">
          <i />
          {organizationLabel(identity)}
          <strong>Operations online</strong>
        </div>
        <p className="nav-group-label">Overview</p>
        <nav>
          <button
            className={view === "overview" ? "active" : ""}
            onClick={() => selectView("overview")}
          >
            <span />
            {homeLabel}
          </button>
          {regionalAdmin && (
            <button
              className={view === "admin" && regionalAdminTab === "weather" ? "active" : ""}
              onClick={() => { setRegionalAdminTab("weather"); selectView("admin"); }}
            >
              <span />
              Blood Weather
            </button>
          )}
          {auditor && (
            <button
              className={view === "admin" ? "active" : ""}
              onClick={() => selectView("admin")}
            >
              <span />
              Operations review
            </button>
          )}
        </nav>
        {identity.role !== "donor" && identity.role !== "hospital_coordinator" && identity.role !== "bank_admin" && (
          <p className="nav-group-label">Operations</p>
        )}
        <nav>
          {regionalAdmin && (
            <button
              className={view === "admin" && regionalAdminTab === "recommendations" ? "active" : ""}
              onClick={() => { setRegionalAdminTab("recommendations"); selectView("admin"); }}
            >
              <span />
              Recommendations
            </button>
          )}
        </nav>
        <p className="nav-group-label">Reference</p>
        <nav>
          {(auditor || regionalAdmin) && (
            <button
              className={view === "audit" ? "active" : ""}
              onClick={() => selectView("audit")}
            >
              <span />
              Audit explorer
            </button>
          )}
          {regionalAdmin && (
            <button
              className={view === "org" ? "active" : ""}
              onClick={() => selectView("org")}
            >
              <span />
              Organization
            </button>
          )}
          <button
            className={view === "account" ? "active" : ""}
            onClick={() => selectView("account")}
          >
            <span />
            Account
          </button>
          <button
            className={view === "privacy" ? "active" : ""}
            onClick={() => selectView("privacy")}
          >
            <span />
            Privacy
          </button>
        </nav>
      </aside>

      <main>
        <header className="topbar">
          <div className="topbar-copy">
            <p className="crumb">BloodNet / {roleNames[identity.role]}</p>
            <h1>{title}</h1>
            <p>
              {view === "overview"
                ? "Here is how your network is looking right now."
                : identity.role === "regional_admin" && view === "admin" && regionalAdminTab === "weather"
                  ? "A seven-day view of projected supply pressure across your region."
                : identity.role === "regional_admin" && view === "admin"
                  ? "Review the operational actions that require your approval."
                  : view === "audit"
                    ? "Search and review the read-only history of operational decisions."
                : "Request-aware coordination across supply, donors, and care teams."}
            </p>
          </div>
          <div className="role-picker">
            <span className="updated">
              <i />
              Updated just now
            </span>
            <div className="identity-summary">
              <strong>{roleNames[identity.role]}</strong>
              <UpdatesCenter
                identity={identity}
                cases={cases}
                recommendations={recommendations}
                pendingRoleRequestCount={pendingRoleRequestCount}
                activeSwarmCount={activeSwarmCount}
                outreachNotificationCount={outreachNotificationCount}
                initials={initialsForIdentity(identity)}
                onOpen={() => {
                  if (identity.role === "regional_admin") {
                    setRegionalAdminTab("recommendations");
                    selectView("admin");
                  } else if (identity.role === "hospital_coordinator" || identity.role === "donor") {
                    selectView(identity.role === "donor" ? "donor" : "hospital");
                  } else {
                    selectView("admin");
                  }
                }}
              />
            </div>
            <button
              className="secondary compact"
              onClick={() => void onSignOut()}
            >
              Sign out
            </button>
          </div>
        </header>

        {notice && <div className="notice">{notice}</div>}
        {caseUpdateErrors.length > 0 && <div className="notice" role="status">Automatic case updates are temporarily unavailable. Retrying…</div>}
        {!identity.location &&
          ["donor", "hospital_coordinator", "bank_admin"].includes(
            identity.role,
          ) && (
            <div className="notice">
              Location access is needed for proximity-based matching. Your exact
              location remains private.{" "}
              <button className="secondary compact" onClick={shareLocation}>
                Share location
              </button>
            </div>
          )}

        {view === "admin" &&
          (identity.role === "bank_admin" ? (
            <BankSurfaceEnhanced identity={identity} />
          ) : identity.role === "regional_admin" && regionalAdminTab === "weather" ? (
            <RegionalSurfaceEnhanced identity={identity} />
          ) : (
            <>
              <AdminFeaturePanels
                api={api}
                cases={cases}
                recommendations={recommendations}
                role={
                  identity.role === "auditor" ? "auditor" : "regional_admin"
                }
                region={identity.region_id || ""}
                section={identity.role === "regional_admin" && regionalAdminTab === "recommendations" ? "recommendations" : undefined}
                onDecision={(id, decision, rationale) =>
                  decide(id, decision, rationale)
                }
              />
              {identity.role === "regional_admin" && regionalAdminTab === "recommendations" && (
                <>
                  <PendingRoleRequestsPanel api={api} />
                  <HospitalCaseTimeline
                    api={api}
                    cases={cases}
                    onCaseUpdated={refreshCases}
                  />
                  <CaseIntelligencePanel cases={cases} />
                </>
              )}
            </>
          ))}
        {view === "donor" && (
          <>
            <DonorOnboarding api={api} />
            <DonorPortal api={api} donorId={identity.subject_id} />
          </>
        )}
        {view === "hospital" && (
          <>
            <HospitalFeaturePanels
              api={api}
              hospitalId={identity.hospital_id || ""}
              cases={cases}
              onCaseUpdated={refreshCases}
            />
            <HospitalJourneyEvidence api={api} cases={cases} />
            <HospitalCaseTimeline
              api={api}
              cases={cases}
              onCaseUpdated={refreshCases}
            />
            <CaseIntelligencePanel cases={cases} />
          </>
        )}
        {view === "audit" && (
          <>
            <AuditInvestigator api={api} />
            <AuditSurfaceEnhanced />
          </>
        )}
        {view === "org" && regionalAdmin && (
          <OrganizationManagementPanel identity={identity} api={api} />
        )}
        {view === "account" && (
          <>
            <AccountManagementPanel identity={identity} api={api} onIdentityUpdated={setIdentity} />
            {identity.role === "donor" && <RoleAccessRequestPanel api={api} />}
          </>
        )}
        {view === "privacy" && <PrivacyPolicyPanel api={api} />}
        {view === "overview" && (
          <RoleHome
            identity={identity}
            api={api}
            cases={cases}
            recommendations={recommendations}
            onCaseUpdated={refreshCases}
            onOpenAdmin={(tab) => {
              setRegionalAdminTab(tab);
              selectView("admin");
            }}
          />
        )}
      </main>
    </div>
  );
}

function RoleHome({
  identity,
  api,
  cases,
  recommendations,
  onCaseUpdated,
  onOpenAdmin,
}: {
  identity: Identity;
  api: AppApi;
  cases: CaseRecord[];
  recommendations: Recommendation[];
  onCaseUpdated: () => Promise<void>;
  onOpenAdmin: (tab: "weather" | "recommendations") => void;
}) {
  if (identity.role === "donor")
    return (
      <>
        <DonorOnboarding api={api} />
        <DonorPortal api={api} donorId={identity.subject_id} />
      </>
    );
  if (identity.role === "hospital_coordinator")
    return (
      <>
        <HospitalFeaturePanels
          api={api}
          hospitalId={identity.hospital_id || ""}
          cases={cases}
          onCaseUpdated={onCaseUpdated}
        />
        <HospitalJourneyEvidence api={api} cases={cases} />
        <HospitalCaseTimeline
          api={api}
          cases={cases}
          onCaseUpdated={onCaseUpdated}
        />
      </>
    );
  if (identity.role === "bank_admin")
    return <BankSurfaceEnhanced identity={identity} />;
  if (identity.role === "auditor")
    return (
      <>
        <AuditInvestigator api={api} />
        <AuditSurfaceEnhanced />
      </>
    );
  return (
    <>
      <Overview
        role={identity.role}
        hospitalId={identity.hospital_id || ""}
        region={identity.region_id || ""}
        cases={cases}
        api={api}
        recommendations={recommendations}
        onOpenAdmin={onOpenAdmin}
      />
    </>
  );
}

function RoleAccessRequestPanel({
  api,
}: {
  api: (path: string, options?: RequestInit) => Promise<unknown>;
}) {
  const [role, setRole] = useState("hospital_coordinator");
  const [organizationName, setOrganizationName] = useState("");
  const [organizationAddress, setOrganizationAddress] = useState("");
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      await api("/match-svc/api/v1/auth/role-requests", {
        method: "POST",
        body: JSON.stringify({
          role,
          organization_name: organizationName,
          organization_address: organizationAddress,
        }),
      });
      setStatus(
        "Request submitted. Access will appear after administrator approval.",
      );
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : "Unable to submit role request",
      );
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="feature-panel wide-feature">
      <div className="feature-head">
        <div>
          <p className="eyebrow">Controlled access</p>
          <h2>Request an operational role</h2>
        </div>
        <CardInfo title="Request an operational role" description="Submit an organization-backed role request for administrator review." />
      </div>
      <p className="feature-note">
        Verify your email, then request access. An administrator reviews and
        activates the membership.
      </p>
      <form className="agent-form" onSubmit={submit}>
        <select value={role} onChange={(event) => setRole(event.target.value)}>
          <option value="hospital_coordinator">Hospital staff</option>
          <option value="bank_admin">Blood bank staff</option>
          <option value="regional_admin">Regional administrator</option>
          <option value="auditor">Auditor</option>
        </select>
        <input
          placeholder="Organization name"
          value={organizationName}
          onChange={(event) => setOrganizationName(event.target.value)}
          required
        />
        <input
          placeholder="Organization address"
          value={organizationAddress}
          onChange={(event) => setOrganizationAddress(event.target.value)}
        />
        <button className="primary" disabled={busy}>
          {busy ? "Submitting..." : "Submit request"}
        </button>
      </form>
      {status && <div className="notice">{status}</div>}
    </section>
  );
}

function PendingRoleRequestsPanel({
  api,
}: {
  api: (path: string, options?: RequestInit) => Promise<unknown>;
}) {
  const [requests, setRequests] = useState<any[]>([]);
  const [status, setStatus] = useState("");
  const [loadError, setLoadError] = useState("");
  const [loading, setLoading] = useState(true);
  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      setRequests((await api("/match-svc/api/v1/auth/role-requests")) as any[]);
    } catch {
      setRequests([]);
      setLoadError("Access requests could not be loaded. Check your connection and retry.");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const decide = async (requestId: string, decision: "approve" | "reject") => {
    try {
      await api(
        `/match-svc/api/v1/auth/role-requests/${requestId}/${decision}`,
        { method: "POST" },
      );
      setStatus(`Access request ${decision}d.`);
      await load();
    } catch (error) {
      setStatus(
        error instanceof Error
          ? error.message
          : "Unable to update access request",
      );
    }
  };
  const pending = requests.filter((item) => item.status === "pending");
  return (
    <section className="feature-panel wide-feature">
      <div className="feature-head">
        <div>
          <p className="eyebrow">Access control</p>
          <h2>Pending access requests</h2>
        </div>
        <CardInfo title="Pending access requests" description="Review requests for roles before approving or rejecting access." />
      </div>
      {status && <div className="notice">{status}</div>}
      {loadError ? <div className="notice error access-load-error" role="alert"><span>{loadError}</span><button className="text-button" onClick={() => void load()} disabled={loading}>{loading ? "Retrying…" : "Retry"}</button></div> : <div
        className="request-table"
        role="table"
        aria-label="Pending access requests"
      >
        <div className="request-row request-header" role="row">
          <strong role="columnheader">Name</strong>
          <strong role="columnheader">Organization</strong>
          <strong role="columnheader">Request for role</strong>
          <strong role="columnheader">Decision</strong>
        </div>
        {pending.map((item) => (
          <div className="request-row" role="row" key={item.id}>
            <span role="cell">
              {item.user?.display_name || item.user?.email || item.user_id}
            </span>
            <span role="cell">
              {item.organization?.name || "Unspecified organization"}
            </span>
            <span role="cell">
              {item.requested_role?.replaceAll("_", " ") || "Unspecified"}
            </span>
            <span className="actions" role="cell">
              <button
                className="secondary compact"
                onClick={() => void decide(item.id, "approve")}
              >
                Approve
              </button>
              <button
                className="secondary compact"
                onClick={() => void decide(item.id, "reject")}
              >
                Reject
              </button>
            </span>
          </div>
        ))}
        {!pending.length && (
          <div className="feature-note">{loading ? "Loading access requests…" : "No pending access requests."}</div>
        )}
      </div>}
    </section>
  );
}

function DonorRankingDetails({
  donors,
}: {
  donors: NonNullable<CaseRecord["ranked_donors"]> | NonNullable<CaseRecord["donor_summary"]>;
}) {
  return (
    <div className="activity-list donor-ranking-details">
      <strong className="activity-section-title">Donor ranking explanation</strong>
      {donors.slice(0, 5).map((donor, index) => (
        <div className="donor-ranking-entry" key={"donor_id" in donor ? donor.donor_id : donor.rank}>
          <strong className="donor-ranking-rank">
            #{index + 1} {donor.blood_group}
          </strong>
          <span className="donor-ranking-metrics">
            {Math.round((donor.success_probability || 0) * 100)}% predicted response{" "}
            {"distance_to_bank_km" in donor ? `· ${donor.distance_to_bank_km ?? "-"} km · ${donor.travel_time_min ?? "-"} min · ${donor.eligibility || "eligible"}` : "· operational summary"}
          </span>
          <small className="donor-ranking-explanation">
            {("explanation" in donor && donor.explanation) ||
              "Ranked by response probability and travel time."}
          </small>
        </div>
      ))}
    </div>
  );
}

function CaseIntelligencePanel({ cases }: { cases: CaseRecord[] }) {
  const [visibleCases, , pagination] = usePagination(cases, 5);
  return (
    <section className="feature-panel wide-feature">
      <div className="feature-head">
        <div>
          <p className="eyebrow">Decision context</p>
          <h2>Donor ranking and round history</h2>
        </div>
        <CardInfo title="Donor ranking and round history" description="Shows the ranked donor evidence and recorded workflow rounds for each case." />
      </div>
      {cases.length ? (
        visibleCases.map((item) => (
          <div className="case-timeline-item" key={item.case.case_id}>
            <strong>{item.case.case_id}</strong>
            {item.ranked_donors?.length || item.donor_summary?.length ? (
              <DonorRankingDetails donors={item.ranked_donors || item.donor_summary || []} />
            ) : (
              <p className="feature-note">
                No eligible donor ranking has been returned for this case.
              </p>
            )}
            <div className="activity-list workflow-rounds">
              <strong className="activity-section-title">Workflow rounds</strong>
              {item.activity?.length ? (
                item.activity.map((event, index) => (
                  <div className="workflow-round" key={`${event.action}-${index}`}>
                    <strong>{event.action.replaceAll("_", " ")}</strong>
                    <span>
                      {new Date(event.at).toLocaleString()} ·{" "}
                      {event.actor || "system"}
                    </span>
                  </div>
                ))
              ) : (
                <span>No persisted round events yet.</span>
              )}
            </div>
          </div>
        ))
      ) : (
        <span className="feature-note">
          No cases in the current hospital scope.
        </span>
      )}
      {pagination}
    </section>
  );
}

function HospitalCaseTimeline({
  api,
  cases,
  onCaseUpdated,
}: {
  api: (path: string, options?: RequestInit) => Promise<unknown>;
  cases: CaseRecord[];
  onCaseUpdated: () => Promise<void>;
}) {
  const [busy, setBusy] = useState("");
  const [visibleCases, , pagination] = usePagination(cases, 5);
  const escalate = async (caseId: string) => {
    const reason = window.prompt(
      "Reason for escalating this case:",
      "Insufficient units after current donor round",
    );
    if (!reason?.trim()) return;
    setBusy(caseId);
    try {
      await api(`/match-svc/api/v1/cases/${caseId}/escalate`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      });
      await onCaseUpdated();
      window.dispatchEvent(new Event("bloodnet:recommendations-changed"));
    } finally {
      setBusy("");
    }
  };
  return (
    <section className="feature-panel wide-feature case-timeline-panel">
      <div className="feature-head">
        <div>
          <p className="eyebrow">Hospital operations</p>
          <h2>Full case timeline</h2>
        </div>
        <CardInfo title="Full case timeline" description="Shows each case from request intake through supply review, mobilization, and outcome." />
      </div>
      {cases.length ? (
        <div className="case-timeline">
          {visibleCases.map((item) => {
            const current = item.case;
            const cancelled =
              current.reservation_state === "cancelled" ||
              current.outcome === "cancelled";
            const fulfilled = current.outcome === "fulfilled";
            const partial =
              current.outcome === "partially_fulfilled" ||
              current.escalation_state === "required";
            const closed = cancelled || fulfilled;
            return (
              <article key={current.case_id} className="case-timeline-item">
                <div className="case-timeline-title">
                  <strong>{current.case_id}</strong>
                  <span
                    className={`badge ${cancelled ? "neutral" : fulfilled ? "success" : partial ? "warning" : "danger"}`}
                  >
                    {current.outcome || current.reservation_state}
                  </span>
                </div>
                <div className="timeline-steps">
                  <span className="done">Request received</span>
                  <span
                    className={
                      current.reservation_state === "awaiting_approval"
                        ? "current"
                        : "done"
                    }
                  >
                    Supply reviewed
                  </span>
                  <span
                    className={
                      current.reservation_state === "reserved"
                        ? "done"
                        : current.reservation_state === "awaiting_approval"
                          ? "current"
                          : ""
                    }
                  >
                    Inventory reserved
                  </span>
                  <span
                    className={partial ? "current" : fulfilled ? "done" : ""}
                  >
                    {current.units_from_donors_remaining > 0
                      ? item.delivery_summary?.total
                        ? `${item.delivery_summary.delivered}/${item.delivery_summary.total} outreach delivered`
                        : "Outreach pending"
                      : "Outreach not required"}
                  </span>
                  <span
                    className={partial ? "current" : fulfilled ? "done" : ""}
                  >
                    {partial ? "Escalation required" : "Fulfillment"}
                  </span>
                </div>
                <small>
                  {current.units_from_inventory} inventory unit(s) ·{" "}
                  {current.units_from_donors_remaining} donor unit(s) remaining
                  {item.request?.source_channel
                    ? ` · received via ${item.request_ingress?.provider || item.request.source_channel}`
                    : ""}
                  {current.escalation_state
                    ? current.escalation_state === "required"
                      ? " · donor mobilization awaiting approval"
                      : ` · ${current.escalation_state.replaceAll("_", " ")}`
                    : ""}
                </small>
                {item.activity?.length ? (
                  <div className="activity-list">
                    {item.activity.map((event, index) => (
                      <div key={`${event.action}-${index}`}>
                        <strong>{event.action.replaceAll("_", " ")}</strong>
                        <span>
                          {new Date(event.at).toLocaleTimeString()} ·{" "}
                          {event.actor || "system"}
                        </span>
                      </div>
                    ))}
                  </div>
                ) : null}
                {!closed && (
                  <div className="timeline-actions">
                    <button
                      className="secondary compact"
                      disabled={busy === current.case_id}
                      onClick={() => void escalate(current.case_id)}
                    >
                      {busy === current.case_id
                        ? "Escalating..."
                        : "Escalate case"}
                    </button>
                  </div>
                )}
              </article>
            );
          })}
          {pagination}
        </div>
      ) : (
        <div className="feature-empty">No cases in the hospital scope.</div>
      )}
    </section>
  );
}

function Overview({
  role,
  hospitalId,
  region,
  cases,
  api,
  recommendations,
  onOpenAdmin,
}: {
  role: Role;
  hospitalId: string;
  region: string;
  cases: CaseRecord[];
  api: (path: string, options?: RequestInit) => Promise<unknown>;
  recommendations: Recommendation[];
  onOpenAdmin: (tab: "weather" | "recommendations") => void;
}) {
  const [rawText, setRawText] = useState("");
  const [status, setStatus] = useState("");
  const [health, setHealth] = useState<{
    data_status?: "available" | "no_active_cases";
    fulfillment_probability?: number | null;
    inventory_coverage?: number | null;
    shortage_exposure?: string | number;
    donor_activity?: number;
  } | null>(null);
  const [healthError, setHealthError] = useState("");
  const [forecastStatus, setForecastStatus] = useState<
    "loading" | "available" | "unavailable" | "error"
  >("loading");
  const [forecastError, setForecastError] = useState("");
  const [forecastFreshness, setForecastFreshness] = useState<{
    latestTargetDate?: string;
    isStale?: boolean;
  }>({});
  const [forecast, setForecast] = useState<
    { target_date: string; blood_group: string; shortage_probability: number }[]
  >([]);
  useEffect(() => {
    setHealthError("");
    void (api("/match-svc/api/v1/network/health") as Promise<typeof health>)
      .then(setHealth)
      .catch((error) => {
        setHealth(null);
        setHealthError(userFacingError(error, "Network health is unavailable."));
      });
    setForecast([]);
    setForecastError("");
    if (region) {
      setForecastStatus("loading");
      void (
        api(
          `/match-svc/api/v1/regional/forecast?region=${encodeURIComponent(region)}&horizon_days=7`,
        ) as Promise<{
          forecast?: typeof forecast;
          latest_target_date?: string;
          is_stale?: boolean;
        }>
      )
        .then((result) => {
          const points = result.forecast || [];
          setForecast(points);
          setForecastFreshness({
            latestTargetDate: result.latest_target_date,
            isStale: result.is_stale,
          });
          setForecastStatus(points.length ? "available" : "unavailable");
        })
        .catch((error) => {
          setForecast([]);
          setForecastFreshness({});
          setForecastStatus("error");
          setForecastError(userFacingError(error, "Blood Weather is unavailable."));
        });
    } else {
      setForecastStatus("unavailable");
    }
  }, [api, region]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api("/intake-svc/api/v1/extract", {
        method: "POST",
        body: JSON.stringify({
          raw_text: rawText,
          hospital_id: hospitalId,
          source_channel: "portal",
        }),
      });
      setStatus("Request structured and queued for matching.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Request failed");
    }
  };

  const forecastDates = selectForecastDates(forecast);
  const forecastGroups = forecastBloodGroups(forecast);
  const forecastSummary = summarizeForecast(forecast);
  const activeCases = cases.filter(isCaseActive);
  const overviewRisk = (probability: number) =>
    probability >= 0.7
      ? "critical"
      : probability >= 0.45
        ? "elevated"
        : probability >= 0.2
          ? "watch"
          : "ok";

  return (
    <div className="overview-grid fun-dashboard">
      <section className="page-head">
        <p className="eyebrow">Network health</p>
        <h2>
          {role === "donor"
            ? "Good morning. Your help matters."
            : "Good morning. Here is the live picture."}
        </h2>
      </section>

      <section className="overview-stats fun-stats">
        {healthError && (
          <div className="notice error overview-data-notice" role="alert">
            {healthError}
          </div>
        )}
        <div className="stat-mint card-with-info">
          <CardInfo title="Fulfillment probability" description="The predicted likelihood that currently active cases can be fulfilled with available inventory and donor response." />
          <span>Fulfillment probability</span>
          <strong>
            {health?.fulfillment_probability == null
              ? "--"
              : `${Math.round(health.fulfillment_probability * 100)}%`}
          </strong>
          <small>Across active cases</small>
        </div>
        <div className="stat-coral card-with-info">
          <CardInfo title="Active critical cases" description="The number of open cases marked critical that need immediate operational attention." />
          <span>Active critical cases</span>
          <strong>
            {
              activeCases.filter((item) => item.request?.urgency === "critical")
                .length
            }
          </strong>
          <small>Needs attention</small>
        </div>
        <div className="stat-mint">
          <span>Inventory coverage</span>
          <strong>
            {health?.inventory_coverage == null
              ? "--"
              : `${Math.round(health.inventory_coverage * 100)}%`}
          </strong>
          <small>Units currently covered</small>
        </div>
        <div className="stat-gold card-with-info">
          <CardInfo title="7-day shortage risk" description="The forecasted exposure to blood shortages across the next seven days in the current scope." />
          <span>7-day shortage risk</span>
          <strong>
            {forecast.length
              ? `${Math.round(forecastSummary.peakProbability * 100)}%`
              : "--"}
          </strong>
          <small>Peak forecast probability</small>
        </div>
        <div className="stat-sky card-with-info">
          <CardInfo title="Donor activity" description="The number of donors currently active or responding to compatible donation opportunities." />
          <span>Donor activity</span>
          <strong>{health?.donor_activity ?? "--"}</strong>
          <small>Active contributors</small>
        </div>
      </section>

      <section className="dashboard-panel forecast-preview">
        <div className="dashboard-panel-head">
          <div>
            <p className="eyebrow">Planning signal</p>
            <h3>Blood Weather</h3>
            <p>
              {forecastFreshness.isStale && forecastFreshness.latestTargetDate
                ? `Most recent published outlook, through ${new Date(`${forecastFreshness.latestTargetDate}T00:00:00`).toLocaleDateString()}.`
                : "Shortage probability over the next seven days."}
            </p>
          </div>
          <CardInfo title="Blood Weather" description="Forecasts shortage probability by blood group over seven days to support proactive supply planning." />
          <button
            className="text-button"
            onClick={() => onOpenAdmin("weather")}
          >
            Open forecast
          </button>
        </div>
        {forecastStatus === "loading" ? (
          <div className="feature-empty">Loading the seven-day forecast…</div>
        ) : forecastDates.length ? (
          <table className="weather-matrix">
            <thead>
              <tr>
                <th />
                {forecastDates.map((date, index) => (
                  <th key={date}>
                    {forecastFreshness.isStale
                      ? new Date(`${date}T00:00:00`).toLocaleDateString(undefined, {
                          month: "short",
                          day: "numeric",
                        })
                      : index === 0
                        ? "Today"
                        : `+${index}`}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {forecastGroups.map((group) => (
                <tr key={group}>
                  <th scope="row">{group}</th>
                  {forecastDates.map((date) => {
                    const probability = forecastCellRisk(forecast, group, date);
                    const risk = probability == null ? "ok" : overviewRisk(probability);
                    return (
                      <td key={`${group}-${date}`}>
                        <span className={`weather-cell risk-${risk}`}>
                          {probability != null
                            ? `${Math.round(probability * 100)}%`
                            : "--"}
                        </span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="feature-empty">
            {forecastStatus === "error"
              ? forecastError
              : `No current forecast has been published for ${region || "this region"}.`}
          </div>
        )}
        <div className="weather-legend">
          <span>
            <i className="risk-ok" />
            Healthy
          </span>
          <span>
            <i className="risk-watch" />
            Watch
          </span>
          <span>
            <i className="risk-elevated" />
            Elevated
          </span>
          <span>
            <i className="risk-critical" />
            Critical
          </span>
        </div>
      </section>

      <section className="dashboard-panel cases-preview">
        <div className="dashboard-panel-head">
          <div>
            <p className="eyebrow">Operations</p>
            <h3>Active cases</h3>
            <p>{activeCases.length || "No"} open in your current scope.</p>
          </div>
          <CardInfo title="Active cases" description="Summarizes open cases that this role can view, including outstanding units and fulfillment likelihood." />
          {(role === "hospital_coordinator" || role === "regional_admin") && (
            <button
              className="text-button"
              onClick={() => onOpenAdmin("recommendations")}
            >
              View all cases
            </button>
          )}
        </div>
        {activeCases.length ? (
          activeCases.slice(0, 4).map((item) => (
            <div className="case-preview-row" key={item.case.case_id}>
              <strong>{item.case.case_id}</strong>
              <span>{item.request?.group || "--"}</span>
              <span>
                {item.case.units_from_donors_remaining} unit(s) outstanding
              </span>
              <span
                className={`pill ${item.case.fulfillment_probability > 0.8 ? "pill-ok" : "pill-high"}`}
              >
                {Math.round(item.case.fulfillment_probability * 100)}% likely
              </span>
            </div>
          ))
        ) : (
          <div className="feature-empty">No active cases in this scope.</div>
        )}
      </section>

      <section className="dashboard-panel action-preview">
        <div className="dashboard-panel-head">
          <div>
            <p className="eyebrow">Human approval boundary</p>
            <h3>Recommendation inbox</h3>
            <p>
              Evidence-backed actions stay here until a person approves them.
            </p>
          </div>
          <CardInfo title="Recommendation inbox" description="Directs authorized users to recommendations that require human approval before any operational action occurs." />
          {operationalRole(role) && (
            <button
              className="text-button"
              onClick={() => onOpenAdmin("recommendations")}
            >
              Open inbox
            </button>
          )}
        </div>
        <div className={`recommendation-summary ${recommendations.length ? "" : "is-empty"}`}>
          <span className="summary-mark">{recommendations.length ? "!" : "✓"}</span>
          <div>
            <strong>
              {recommendations.length
                ? `${recommendations.length} decision${recommendations.length === 1 ? "" : "s"} awaiting review`
                : "No decisions awaiting review"}
            </strong>
            <p>
              {recommendations.length
                ? "Open the inbox to review the latest evidence-backed operational actions."
                : "New forecast, inventory, and donor recommendations will appear here when available."}
            </p>
          </div>
        </div>
      </section>

      {role === "hospital_coordinator" && (
        <section className="feature-panel intake-panel dashboard-intake">
          <div className="feature-head">
            <div>
              <p className="eyebrow">Hospital portal</p>
              <h2>Submit a request</h2>
            </div>
            <CardInfo title="Submit a request" description="Creates a hospital case from the entered request details and sends it to the matching workflow." />
          </div>
          <form className="agent-form" onSubmit={submit}>
            <textarea
              value={rawText}
              onChange={(event) => setRawText(event.target.value)}
            />
            <button className="primary">Create case</button>
          </form>
          {status && <div className="notice">{status}</div>}
        </section>
      )}
    </div>
  );
}

function operationalRole(role: Role) {
  return (
    role === "regional_admin" || role === "bank_admin" || role === "auditor"
  );
}

function OrganizationManagementPanel({
  identity,
  api,
}: {
  identity: Identity;
  api: (path: string, options?: RequestInit) => Promise<unknown>;
}) {
  const [organizations, setOrganizations] = useState<any[]>([]);
  const [members, setMembers] = useState<any[]>([]);
  const [roleRequests, setRoleRequests] = useState<any[]>([]);
  const [selectedOrganization, setSelectedOrganization] = useState("");
  const [regionId, setRegionId] = useState("");
  const [status, setStatus] = useState("");
  const [searchTerm, setSearchTerm] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize] = useState(10);
  const [form, setForm] = useState({
    name: "",
    type: "hospital",
    contact_email: "",
    address: "",
  });

  const filteredOrganizations = useMemo(() => {
    const term = searchTerm.trim().toLowerCase();
    if (!term) return organizations;

    return organizations.filter((organization) => {
      const haystack = [
        organization.name,
        organization.type,
        organization.status,
        organization.contact_email,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(term);
    });
  }, [organizations, searchTerm]);

  const totalPages = Math.max(1, Math.ceil(filteredOrganizations.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const paginatedOrganizations = filteredOrganizations.slice(
    (safePage - 1) * pageSize,
    safePage * pageSize,
  );

  useEffect(() => {
    setPage(1);
  }, [searchTerm]);

  const load = async () => {
    const result = (await api("/match-svc/api/v1/organizations")) as any[];
    const nextSelectedOrganization =
      selectedOrganization && result.some((item) => item.id === selectedOrganization)
        ? selectedOrganization
        : result[0]?.id || "";

    setOrganizations(result);
    setSelectedOrganization(nextSelectedOrganization);

    const selected = result.find((item) => item.id === nextSelectedOrganization) || null;
    setRegionId(String(selected?.metadata?.region_id || ""));

    if (nextSelectedOrganization) {
      setMembers(
        (await api(
          `/match-svc/api/v1/organizations/${nextSelectedOrganization}/members`,
        )) as any[],
      );
    } else {
      setMembers([]);
    }

    setRoleRequests(
      (await api("/match-svc/api/v1/auth/role-requests")) as any[],
    );
  };

  useEffect(() => {
    void load().catch((error) =>
      setStatus(
        error instanceof Error ? error.message : "Unable to load organizations",
      ),
    );
  }, []);

  useEffect(() => {
    if (!selectedOrganization) return;
    void load().catch((error) =>
      setStatus(
        error instanceof Error ? error.message : "Unable to refresh organization details",
      ),
    );
  }, [selectedOrganization]);
  const create = async (event: FormEvent) => {
    event.preventDefault();
    try {
      await api("/match-svc/api/v1/organizations", {
        method: "POST",
        body: JSON.stringify({ ...form, metadata: {} }),
      });
      setForm({ name: "", type: "hospital", contact_email: "", address: "" });
      setStatus("Organization created and awaiting verification.");
      await load();
    } catch (error) {
      setStatus(
        error instanceof Error ? error.message : "Organization creation failed",
      );
    }
  };
  const mutate = async (path: string, options?: RequestInit) => {
    try {
      await api(path, options);
      setStatus("Organization access updated.");
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Update failed");
    }
  };
  const saveRegion = async () => {
    if (!selectedOrganization || !regionId.trim()) { setStatus("Select an organization and enter its operational region."); return; }
    await mutate(`/match-svc/api/v1/organizations/${selectedOrganization}/region`, { method: "PATCH", body: JSON.stringify({ region_id: regionId }) });
  };
  return (
    <div className="feature-grid">
      <section className="feature-panel wide-feature">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Organization</p>
            <h2>
              {identity.organization?.name ||
                "Organization context unavailable"}
            </h2>
          </div>
          <CardInfo title="Organization context" description="Shows the active organization's type, regional scope, and identifier used to filter this session's data." />
        </div>
        <div className="health-grid">
          <div>
            <strong>{identity.organization?.type || "-"}</strong>
            <span>organization type</span>
          </div>
          <div>
            <strong>{identity.region_id || "-"}</strong>
            <span>regional scope</span>
          </div>
          <div>
            <strong>{identity.organization?.id || "-"}</strong>
            <span>organization ID</span>
          </div>
        </div>
      </section>

      <section className="feature-panel wide-feature">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Platform administration</p>
            <h2>Organizations</h2>
          </div>
          <CardInfo title="Organizations" description="Creates, verifies, and selects organizations; the selected organization is used for membership and region administration." />
        </div>
        {status && <div className="notice">{status}</div>}
        {roleRequests.filter((item) => item.status === "pending").length > 0 && (
          <div className="notice">
            {roleRequests.filter((item) => item.status === "pending").length} access request
            {roleRequests.filter((item) => item.status === "pending").length === 1 ? "" : "s"} awaiting review.
          </div>
        )}
        <form className="agent-form" onSubmit={create}>
          <input
            placeholder="Organization name"
            value={form.name}
            onChange={(event) => setForm({ ...form, name: event.target.value })}
            required
          />
          <select
            value={form.type}
            onChange={(event) => setForm({ ...form, type: event.target.value })}
          >
            <option value="hospital">Hospital</option>
            <option value="blood_bank">Blood bank</option>
            <option value="regional">Regional</option>
          </select>
          <input
            type="email"
            placeholder="Contact email"
            value={form.contact_email}
            onChange={(event) =>
              setForm({ ...form, contact_email: event.target.value })
            }
            required
          />
          <input
            placeholder="Address"
            value={form.address}
            onChange={(event) =>
              setForm({ ...form, address: event.target.value })
            }
          />
          <button className="primary">Create organization</button>
        </form>
        <div className="agent-form" style={{ gridTemplateColumns: "1fr" }}>
          <input
            placeholder="Search organizations"
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
          />
        </div>
        <div className="history-list">
          {paginatedOrganizations.map((organization) => (
            <div key={organization.id}>
              <strong>{organization.name}</strong>
              <span>
                {organization.type} · {organization.status}{" "}
                <button
                  className="secondary compact"
                  onClick={() => {
                    setSelectedOrganization(organization.id);
                    setStatus(`Managing ${organization.name}.`);
                  }}
                >
                  Manage
                </button>
                {organization.status !== "active" && (
                  <button
                    className="secondary compact"
                    onClick={() =>
                      void mutate(
                        `/match-svc/api/v1/organizations/${organization.id}/verify`,
                        { method: "POST" },
                      )
                    }
                  >
                    Verify
                  </button>
                )}
              </span>
            </div>
          ))}
          {!paginatedOrganizations.length && (
            <span className="feature-note">No organizations match the current search.</span>
          )}
        </div>
        {filteredOrganizations.length > pageSize && (
          <div className="pagination-controls" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "0.75rem" }}>
            <button
              className="secondary compact"
              disabled={safePage <= 1}
              onClick={() => setPage((current) => Math.max(1, current - 1))}
            >
              Previous
            </button>
            <span className="feature-note">
              Page {safePage} of {totalPages}
            </span>
            <button
              className="secondary compact"
              disabled={safePage >= totalPages}
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
            >
              Next
            </button>
          </div>
        )}
        <div className="region-form-row">
          <label htmlFor="operational-region">Selected organization region</label>
          <div className="region-form-controls">
            <select id="operational-region" value={regionId} onChange={(event) => setRegionId(event.target.value)} required>
              <option value="">Select an operational city</option>
              {INDIAN_CITIES.map((option) => <option key={option} value={option}>{option}</option>)}
            </select>
            <button type="button" className="primary compact" onClick={() => void saveRegion()}>Save region</button>
          </div>
        </div>
      </section>

      <section className="feature-panel wide-feature membership-panel">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Memberships</p>
            <h2>Organization members</h2>
          </div>
          <CardInfo title="Organization members" description="Lists people already connected to this organization. Activate a pending membership or change a member’s operational role; that role determines their ongoing organization access." />
        </div>
        <p className="feature-note">A membership is the person’s organization record. It can be active or awaiting activation.</p>
        <div className="history-list">
          {members.map((item) => (
            <div key={item.membership.id}>
              <strong>{item.user?.email || item.membership.user_id}</strong>
              <span>
                {item.membership.role} · {item.membership.status}{" "}
                <select
                  value={item.membership.role}
                  onChange={(event) =>
                    void mutate(
                      `/match-svc/api/v1/organizations/${selectedOrganization}/members/${item.membership.id}/role?role=${encodeURIComponent(event.target.value)}`,
                      { method: "POST" },
                    )
                  }
                >
                  <option value="donor">donor</option>
                  <option value="hospital_coordinator">
                    hospital_coordinator
                  </option>
                  <option value="bank_admin">bank_admin</option>
                  <option value="regional_admin">regional_admin</option>
                  <option value="auditor">auditor</option>
                </select>
                {item.membership.status === "pending_approval" && (
                  <button
                    className="secondary compact"
                    onClick={() =>
                      void mutate(
                        `/match-svc/api/v1/organizations/${selectedOrganization}/members/${item.membership.id}/approve`,
                        { method: "POST" },
                      )
                    }
                  >
                    Approve
                  </button>
                )}
              </span>
            </div>
          ))}
          {!members.length && <span className="feature-note">No members are available for the selected organization.</span>}
        </div>
      </section>

      <section className="feature-panel">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Role requests</p>
            <h2>Access requests awaiting decision</h2>
          </div>
          <CardInfo title="Access requests awaiting decision" description="Lists applications from people who want a role at an organization. Approving one creates or updates their membership; rejecting it grants no access." />
        </div>
        <p className="feature-note">A role request is an application. It becomes an organization membership only after approval.</p>
        <div className="history-list">
          {roleRequests
            .filter((item) => item.status === "pending")
            .map((item) => (
              <div key={item.id}>
                <strong>
                  {item.user?.display_name || item.user?.email || item.user_id}
                </strong>
                <span>
                  {item.requested_role} ·{" "}
                  {item.organization?.name || item.organization_id}{" "}
                  <button
                    className="secondary compact"
                    onClick={() =>
                      void mutate(
                        `/match-svc/api/v1/auth/role-requests/${item.id}/approve`,
                        { method: "POST" },
                      )
                    }
                  >
                    Approve
                  </button>
                  <button
                    className="secondary compact"
                    onClick={() =>
                      void mutate(
                        `/match-svc/api/v1/auth/role-requests/${item.id}/reject`,
                        { method: "POST" },
                      )
                    }
                  >
                    Reject
                  </button>
                </span>
              </div>
            ))}
          {!roleRequests.some((item) => item.status === "pending") && (
            <span className="feature-note">No pending role requests.</span>
          )}
        </div>
      </section>

    </div>
  );
}

function AccountManagementPanel({
  identity,
  api,
  onIdentityUpdated,
}: {
  identity: Identity;
  api: AppApi;
  onIdentityUpdated: (identity: Identity) => void;
}) {
  const [session, setSession] = useState<{
    last_signin?: string;
    mfa_enabled?: boolean | null;
    email_verified?: boolean | null;
    recovery_email?: string;
  } | null>(null);
  const [activity, setActivity] = useState<
    { action: string; timestamp: string }[]
  >([]);
  const [preferredRegion, setPreferredRegion] = useState("");
  const [regionEditing, setRegionEditing] = useState(true);
  const [regionStatus, setRegionStatus] = useState("");
  const [regionSaving, setRegionSaving] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [activityError, setActivityError] = useState("");

  useEffect(() => {
    if (identity.role !== "donor") {
      void (api as any)("/match-svc/api/v1/me/session")
        .then((data: any) => setSession(data.session || {}))
        .catch((error: unknown) => {
          setSession(null);
          setSessionError(error instanceof Error ? error.message : "Session details are unavailable.");
        });

      void (api as any)("/match-svc/api/v1/audit/activity")
        .then((data: any) => setActivity((data.events || []).map((event: any) => ({
          action: event.action,
          timestamp: event.timestamp || event.at || "Timestamp unavailable",
        }))))
        .catch((error: unknown) => {
          setActivity([]);
          setActivityError(error instanceof Error ? error.message : "Account activity is unavailable.");
        });
    }

    if (identity.role !== "donor") {
      if (identity.role === "regional_admin") {
        setPreferredRegion(identity.region_id || "");
        setRegionEditing(!identity.region_id);
      } else {
        void (api as any)("/match-svc/api/v1/auth/region-preference")
          .then((data: any) => { setPreferredRegion(data.region_id || ""); setRegionEditing(!data.region_id); })
          .catch(() => undefined);
      }
    }
  }, [api, identity.role]);

  const savePreferredRegion = async () => {
    if (!preferredRegion.trim()) {
      setRegionStatus("Choose or enter a region before saving.");
      return;
    }
    setRegionSaving(true);
    setRegionStatus("");
    try {
      const saved = identity.role === "regional_admin"
        ? await (api as any)(`/match-svc/api/v1/organizations/${encodeURIComponent(identity.organization?.id || "")}/region`, {
            method: "PATCH",
            body: JSON.stringify({ region_id: preferredRegion }),
          })
        : await (api as any)("/match-svc/api/v1/auth/region-preference", {
            method: "PUT",
            body: JSON.stringify({ region_id: preferredRegion }),
          });
      setPreferredRegion(saved.metadata?.region_id || saved.region_id || preferredRegion.trim());
      setRegionEditing(false);
      const refreshedIdentity = await (api as any)("/match-svc/api/v1/me");
      onIdentityUpdated(refreshedIdentity as Identity);
      setRegionStatus(identity.role === "regional_admin" ? "Administrative region updated." : "Personal service region saved.");
    } catch (error) {
      setRegionStatus(
        error instanceof Error
          ? error.message
          : "We couldn't save your region. Please try again.",
      );
    } finally {
      setRegionSaving(false);
    }
  };

  return (
    <div className="feature-grid account-management-grid">
      <section className="feature-panel account-state-panel">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Current identity</p>
            <h2>Account state</h2>
          </div>
          <CardInfo title="Account state" description="Shows who is signed in, their active organization role, and the permission scope applied to this session." />
        </div>
        <div className="history-list">
          {identity.role !== "donor" && <div>
            <strong>Signed in as</strong>
            <span>{identity.display_name || identity.email || roleNames[identity.role]}</span>
          </div>}
          <div>
            <strong>Subject</strong>
            <span>{identity.subject_id}</span>
          </div>
          <div>
            <strong>Role</strong>
            <span>{roleNames[identity.role]}</span>
          </div>
          <div>
            <strong>Session</strong>
            <span>Authenticated</span>
          </div>
          <div>
            <strong>Organization</strong>
            <span>{identity.role === "donor" ? "None" : identity.organization?.name || "Current organization"}</span>
          </div>
          <div>
            <strong>Authenticated scope</strong>
            <span>{identity.region_id || identity.bank_id || identity.hospital_id || "Account default"}</span>
          </div>
          <div>
            <strong>Membership management</strong>
            <span>{identity.role === "regional_admin" ? "Allowed for your organization" : "Managed by a regional administrator"}</span>
          </div>
        </div>
      </section>

      {identity.role !== "donor" && <section className="feature-panel wide-feature account-region-panel">
        <div className="feature-head">
          <div>
            <p className="eyebrow">{identity.role === "regional_admin" ? "Organization scope" : "Location preference"}</p>
            <h2>{identity.role === "regional_admin" ? "Your administrative region" : "Your service region"}</h2>
          </div>
          <CardInfo title={identity.role === "regional_admin" ? "Your administrative region" : "Your service region"} description={identity.role === "regional_admin" ? "Updates the operational region assigned to your organization. This controls the regional data and decisions in your workspace." : "Saves the region you use for relevant service information and future local notifications."} />
        </div>
        {regionEditing ? <div className="region-preference-form">
          <label htmlFor="preferred-region">Region</label>
          <div>
            <select id="preferred-region" value={preferredRegion} onChange={(event) => setPreferredRegion(event.target.value)}>
              <option value="">Select an Indian city</option>
              {INDIAN_CITIES.map((city) => <option key={city} value={city}>{city}</option>)}
            </select>
            <button type="button" className="primary compact" onClick={() => void savePreferredRegion()} disabled={regionSaving}>{regionSaving ? "Saving…" : "Save region"}</button>
          </div>
        </div> : <div className="saved-region">
          <div className="saved-profile-header"><span className="badge neutral">Saved</span><button type="button" className="secondary compact" onClick={() => { setRegionEditing(true); setRegionStatus(""); }}>Edit region</button></div>
          <div className="saved-region-value"><strong>{identity.role === "regional_admin" ? "Administrative region" : "Service region"}</strong><span>{preferredRegion}</span></div>
        </div>}
        {regionStatus && <p className="region-status" role="status">{regionStatus}</p>}
        {identity.region_id && identity.role !== "regional_admin" && <p className="scope-note">Your current organization access scope is <strong>{identity.region_id}</strong>. Manage that separately in Organization.</p>}
      </section>}

      {identity.role !== "donor" && (
        <FacilityLocationEditor api={api} savedLocation={identity.location} />
      )}

      {identity.role !== "donor" && <>
        <section className="feature-panel account-security-panel">
          <div className="feature-head">
            <div>
              <p className="eyebrow">Security</p>
              <h2>Session controls</h2>
            </div>
            <CardInfo title="Session controls" description="Displays the security status of the current sign-in, including multi-factor authentication and recovery settings." />
          </div>
          <div className="history-list">
            <div>
              <strong>MFA</strong>
              <span>{session?.mfa_enabled == null ? "Not reported" : session.mfa_enabled ? "Enabled" : "Not configured"}</span>
            </div>
            <div>
              <strong>Last sign-in</strong>
              <span>{session?.last_signin || "Not reported"}</span>
            </div>
            <div>
              <strong>Recovery</strong>
              <span>
                {session?.recovery_email
                  ? session.email_verified === true
                    ? "Primary email verified"
                    : "Primary email on file"
                  : "Not configured"}
              </span>
            </div>
            <div>
              <strong>Actions</strong>
              <span>Reset password / sign out</span>
            </div>
          </div>
          {sessionError && <p className="feature-note" role="status">{sessionError}</p>}
        </section>

        <section className="feature-panel wide-feature account-activity-panel">
          <div className="feature-head">
            <div>
              <p className="eyebrow">Audit & provenance</p>
              <h2>Recent account activity</h2>
            </div>
            <CardInfo title="Recent account activity" description="Shows recent security and account events available to the authenticated user." />
          </div>
          <div className="history-list">
            {activity.map((item, index) => (
              <div key={index}>
                <strong>{item.action}</strong>
                <span>{item.timestamp}</span>
              </div>
            ))}
            {!activity.length && <span className="feature-note">{activityError || "No recent account activity."}</span>}
          </div>
        </section>
      </>}
    </div>
  );
}

function PrivacyPolicyPanel({
  api,
}: {
  api: (path: string, options?: RequestInit) => Promise<unknown>;
}) {
  const [policies, setPolicies] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    void (api as any)("/match-svc/api/v1/privacy/policies")
      .then((data: any) => setPolicies(data.policies || []))
      .catch((reason: unknown) => {
        setPolicies([]);
        setError(reason instanceof Error ? reason.message : "Privacy policies are unavailable.");
      })
      .finally(() => setLoading(false));
  }, [api]);

  const policyChecks = policies;
  const policyLabels = [
    "Who can see donor information",
    "Information needed for the task",
    "Who can see investigation details",
    "How donor details are hidden",
  ];

  return (
    <div className="feature-grid">
      <section className="feature-panel wide-feature">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Privacy controls</p>
            <h2>Data minimization</h2>
          </div>
          <CardInfo title="Data minimization" description="Lists the privacy rules that limit displayed data to what is necessary for the current operational task." />
        </div>
        <div className="history-list">
          {policyChecks.map((check, index) => (
            <div key={index}>
              <strong>{policyLabels[index] || "Privacy rule"}</strong>
              <span>{check}</span>
            </div>
          ))}
          {!policyChecks.length && <span className="feature-note">{loading ? "Loading privacy policies…" : error || "No privacy policies are available."}</span>}
        </div>
      </section>

      <section className="feature-panel">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Redaction</p>
            <h2>Safe display rules</h2>
          </div>
          <CardInfo title="Safe display rules" description="Explains which donor identifiers, contact details, and recommendation evidence are masked for this role." />
        </div>
        <div className="history-list">
          <div>
            <strong>Donor IDs</strong>
            <span>Masked in hospital and admin summaries</span>
          </div>
          <div>
            <strong>Phone</strong>
            <span>Hidden outside donor self-service</span>
          </div>
          <div>
            <strong>Recommendation evidence</strong>
            <span>Visible only with authorization</span>
          </div>
        </div>
      </section>

      <section className="feature-panel">
        <div className="feature-head">
          <div>
            <p className="eyebrow">Review</p>
            <h2>Approval boundaries</h2>
          </div>
          <CardInfo title="Approval boundaries" description="Clarifies which roles may inspect evidence, use read-only audit access, or view only their own records." />
        </div>
        <div className="history-list">
          <div>
            <strong>Admin</strong>
            <span>Can inspect evidence and tool traces</span>
          </div>
          <div>
            <strong>Auditor</strong>
            <span>Read-only compliance access</span>
          </div>
          <div>
            <strong>Donor</strong>
            <span>Own consent and request status only</span>
          </div>
        </div>
      </section>
    </div>
  );
}
