import { FormEvent, useEffect, useState } from "react";
import { RecommendationDetailDialog, RecommendationResult } from "./RecommendationUI";
import type { DecisionResult, Recommendation } from "./RecommendationUI";
import { CardInfo } from "./CardInfo";
import { userFacingError } from "./apiClient";
import { INDIAN_CITIES } from "./indiaLocations";
import { forecastBloodGroups, forecastCellRisk, forecastDates as selectForecastDates } from "./forecastPresentation";
import { usePagination } from "./Pagination";

type Api = <T>(path: string, options?: RequestInit) => Promise<T>;

export function InventoryIntakeForm({ api, bankId, onSaved }: { api: Api; bankId: string; onSaved: () => void }) {
  const [unitId, setUnitId] = useState("");
  const [group, setGroup] = useState("O+");
  const [component, setComponent] = useState("RBC");
  const [collectedAt, setCollectedAt] = useState("");
  const [expiresAt, setExpiresAt] = useState("");
  const [eventId, setEventId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      await api("/match-svc/api/v1/integrations/blood-bank/inventory", { method: "POST", body: JSON.stringify({
        source_system: "blood-bank-portal", source_event_id: eventId,
        units: [{ unit_id: unitId.trim(), bank_id: bankId, group, component,
          collected_at: new Date(collectedAt).toISOString(), expires_at: new Date(expiresAt).toISOString(), status: "available" }],
      }) });
      setStatus(`Unit ${unitId.trim()} recorded.`);
      setUnitId("");
      setEventId(crypto.randomUUID());
      onSaved();
    } catch (error) {
      setStatus(userFacingError(error, "The unit could not be recorded. Please check its details."));
    } finally { setBusy(false); }
  };
  return <section className="feature-panel">
    <div className="feature-head"><div><p className="eyebrow">Inventory intake</p><h2>Record a blood unit</h2></div><CardInfo title="Record a blood unit" description="Adds a verified blood unit to this bank's available inventory." /></div>
    <form className="agent-form" onSubmit={submit} onChange={() => setEventId(crypto.randomUUID())}>
      <p>Enter the unit's label and verified collection and expiry times.</p>
      <div className="structured-request">
        <label>Unit barcode<input value={unitId} onChange={(event) => setUnitId(event.target.value)} maxLength={128} required disabled={busy} /></label>
        <label>Unit blood group<select value={group} onChange={(event) => setGroup(event.target.value)} disabled={busy}>{["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Unit component<select value={component} onChange={(event) => setComponent(event.target.value)} disabled={busy}>{["RBC", "Whole Blood", "Platelets (RDP)", "Platelets (SDP)", "FFP", "Cryoprecipitate"].map((value) => <option key={value}>{value}</option>)}</select></label>
        <label>Collected at<input type="datetime-local" value={collectedAt} onChange={(event) => setCollectedAt(event.target.value)} required disabled={busy} /></label>
        <label>Expires at<input type="datetime-local" value={expiresAt} onChange={(event) => setExpiresAt(event.target.value)} required disabled={busy} /></label>
      </div>
      <button className="primary" disabled={busy || !unitId.trim()}>{busy ? "Recording…" : "Record unit"}</button>
      {status && <p role="status">{status}</p>}
    </form>
  </section>;
}
const formatTravelTime = (hours?: number | null) => hours != null && hours > 0 ? `${hours.toFixed(1)} h` : hours === 0 ? "same-city route" : "travel time unavailable";
type CaseRecord = { case: { case_id: string; request_id: string; fulfillment_probability: number; reservation_state: string; units_from_inventory: number; units_from_donors_remaining: number; units_from_donors_fulfilled?: number; confirmed_inventory_units?: number; confirmed_donor_units?: number; outcome?: string; escalation_state?: string }; request?: { group?: string; component?: string; qty?: number; hospital_id?: string; urgency?: string; required_by?: string; source_channel?: string }; request_ingress?: { provider: string; provider_event_id: string; status: string; received_at: string; processed_at?: string }; delivery_summary?: { total: number; delivered: number; failed: number; statuses: Record<string, number> }; supply_resilience?: { supplier_count: number; stocked_supplier_count: number; single_supplier_dependency: boolean; alternative_suppliers: number }; ranked_donors?: Array<{ donor_id: string; blood_group: string; success_probability: number; distance_to_bank_km?: number; travel_time_min?: number; eligibility?: string; explanation?: string }> };
type ForecastPoint = { target_date: string; blood_group: string; component: string; shortage_probability: number; predicted_demand: number; projected_supply: number };
type NetworkHealth = { active_cases: number; fulfillment_probability: number | null; shortage_exposure: number; inventory_coverage: number | null; donor_activity: number; escalated_cases: number; notifications_pending: number; notifications_failed: number; notifications_delivered: number; status: string };
type GraphDataset = { supply_edges: unknown[]; donor_edges: unknown[]; demand_by_region: Record<string, number> };
type IntegrationRole = "hospital_coordinator" | "bank_admin" | "regional_admin" | "auditor";
type SopStatus = { ready: boolean; enabled: boolean; document_count: number; embedded_count: number; last_updated?: string; retrieval: string; access: string; citations_required: boolean };
type SopPassage = { document_id: string; title: string; content: string; citation: string; similarity?: number | null };
type MessagingEvent = { provider: string; provider_event_id: string; request_id: string; hospital_id: string; status: string; received_at: string };
type DeliveryOperation = { event_id: string; request_id: string; status: string; attempts: number; delivery_status: string; next_retry_at?: string; terminal_failure: boolean };
type NetworkBriefing = {
  summary: { banks: number; hospitals: number; relationships: number; vulnerable_hospitals: number; redistribution_opportunities: number };
  critical_nodes: Array<{ bank_id: string; region?: string; importance_score: number; served_hospitals: number; dependent_hospitals: string[]; transfer_connections: number; available_units: number }>;
  vulnerabilities: Array<{ unavailable_bank_id: string; affected_hospitals: string[]; vulnerable_hospitals: string[]; alternatives: Record<string, Array<{ bank_id: string; available_units: number; travel_time_hours: number }>> }>;
  redistribution_opportunities: Array<{ from_bank: string; to_bank: string; blood_group: string; component: string; suggested_units: number; travel_time_hours?: number | null; reason: string; requires_approval: boolean }>;
  topology: { nodes: Array<{ id: string; type: string; label: string }>; edges: Array<{ source: string; target: string; type: string }> };
};

const panelDescriptions: Record<string, string> = {
  "Operational pulse": "Summarizes open cases, predicted fulfillment, shortage exposure, donor activity, and notification delivery health for the selected region.",
  "Shortage probability, next 7 days": "Compares daily forecasted shortage probability by blood group so regional teams can prioritize supply actions.",
  "Recommendation inbox": "Lists evidence-backed interventions awaiting human review; approval is required before an operational action is executed.",
  "Agent chat": "Lets an authorized user ask the read-only analyst about current operational risk.",
  "Graph explorer": "Runs network-graph analyses such as weak coverage, supplier concentration, and load-bearing donor identification.",
  "Consent controls": "Controls whether BloodNet may contact the donor for compatible donation opportunities.",
  "Donation history": "Shows completed donor outreach and donation-related activity recorded for this account.",
  "Request card": "Presents active compatible donation requests and lets the donor accept or decline each outreach.",
  "Donation readiness": "Shows eligibility and the next date on which the donor can be considered for a donation.",
  "Response history": "Records previous donor responses and donation activity so the donor can track their participation.",
  "Submit request": "Captures a hospital blood request, extracts structured clinical details, and requires review before creating a case.",
  "Active cases": "Tracks the hospital's open requests, their matching progress, and available fulfillment actions.",
  "Bank inventory": "Shows affiliated blood-bank units available to support the hospital's active cases.",
  "Graph analysis": "Explores relationships between supply, demand, and donors for the selected graph-analysis question.",
  "Entities": "Searches network entities and displays their relationships in the operational graph.",
  "Find events": "Finds workflow events by action, delivery receipt state, or SOP citation for investigation and compliance review.",
  "Analysis output": "Displays the result and supporting data returned by the selected graph analysis.",
  "Upload an order": "Extracts a draft blood request from a hospital PDF or text order; patient identifiers are not retained by this adapter.",
  "Ingest FHIR or HL7 order": "Validates and ingests a provider-issued FHIR ServiceRequest or HL7v2 order into the clinical intake workflow.",
  "Your donor profile": "Shows the donor information and availability settings used for compatible outreach.",
  "SOP evidence explorer": "Searches only the approved SOP corpus and displays the citation attached to every retrieved passage.",
  "Inbound channel activity": "Shows idempotently processed SMS and WhatsApp provider events without exposing sender identifiers or message bodies.",
  "Delivery operations": "Shows durable outbox handoff, provider delivery state, attempts, retry timing, and worker leases.",
};
const eyebrowDescriptions: Record<string, string> = {
};

function Panel({ eyebrow, title, children, className = "", optional = false }: { eyebrow: string; title: string; children: React.ReactNode; className?: string; optional?: boolean }) {
  return <section className={`feature-panel ${className}`}><div className="feature-head"><div><p className="eyebrow">{eyebrow}</p><h2>{title}</h2></div><div className="card-head-actions"><CardInfo title={title} description={panelDescriptions[title] || eyebrowDescriptions[eyebrow] || "Provides the controls and current operational data for this workflow."} />{optional && <span className="badge neutral">Beta</span>}</div></div>{children}</section>;
}

function EmptyFeature({ children }: { children: string }) { return <div className="feature-empty">{children}</div>; }

function InlineSopEvidence({ api, query }: { api: Api; query: string }) {
  const [passages, setPassages] = useState<SopPassage[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const search = async () => {
    setBusy(true); setMessage("");
    try {
      const result = await api<{ passages: SopPassage[] }>("/agent-svc/api/v1/sops/search", { method: "POST", body: JSON.stringify({ query }) });
      setPassages(result.passages.slice(0, 3));
      if (!result.passages.length) setMessage("No approved SOP guidance matched this request.");
    } catch (error) { setMessage(userFacingError(error, "SOP guidance is unavailable.")); }
    finally { setBusy(false); }
  };
  return <div className="inline-evidence"><div className="inline-evidence-head"><div><strong>Relevant SOP guidance</strong><small>Read-only, approved corpus</small></div><CardInfo title="Relevant SOP guidance" description="Retrieves approved SOP passages for this decision. Each passage must carry a stored citation; guidance does not execute or approve an action." /><button type="button" className="text-button" onClick={() => void search()} disabled={busy}>{busy ? "Finding guidance..." : passages.length ? "Refresh guidance" : "Find guidance"}</button></div>{message && <div className="notice">{message}</div>}{passages.map((passage) => <article key={`${passage.document_id}-${passage.citation}`}><div><strong>{passage.title}</strong><span className="badge success">Cited</span></div><p>{passage.content}</p><cite>{passage.citation}</cite></article>)}</div>;
}

export function NetworkIntelligencePanel({ api, region, canRecommend = false }: { api: Api; region: string; canRecommend?: boolean }) {
  const [briefing, setBriefing] = useState<NetworkBriefing | null>(null);
  const [snapshotId, setSnapshotId] = useState("");
  const [stale, setStale] = useState(false);
  const [status, setStatus] = useState<"loading" | "ready" | "empty" | "unavailable">("loading");
  const [creating, setCreating] = useState("");
  const [message, setMessage] = useState("");
  const load = async (refresh = false) => {
    setStatus("loading");
    setStale(false);
    try {
      const result = await api<{ data: NetworkBriefing; snapshot_id?: string; stale?: boolean }>("/graph-svc/api/v1/graph/briefing", {
        method: "POST",
        body: JSON.stringify({ analysis_type: "network_briefing", params: region ? { region, ...(refresh ? { refresh: true } : {}) } : {} }),
      });
      setBriefing(result.data);
      setSnapshotId(result.snapshot_id || "");
      setStale(Boolean(result.stale));
      setStatus(result.data.summary.relationships ? "ready" : "empty");
    } catch {
      setBriefing(null);
      setStatus("unavailable");
    }
  };
  useEffect(() => { void load(); }, [api, region]);
  useEffect(() => {
    const refreshRestoredPage = (event: PageTransitionEvent) => {
      if (event.persisted) void load();
    };
    window.addEventListener("pageshow", refreshRestoredPage);
    return () => window.removeEventListener("pageshow", refreshRestoredPage);
  }, [api, region]);
  const createRecommendation = async (item: NetworkBriefing["redistribution_opportunities"][number]) => {
    const key = `${item.from_bank}-${item.to_bank}-${item.blood_group}-${item.component}`;
    setCreating(key); setMessage("");
    try {
      const result = await api<{ recommendation: { rec_id: string }; created: boolean }>("/match-svc/api/v1/network/redistribution-recommendations", {
        method: "POST",
        body: JSON.stringify({ ...item, snapshot_id: snapshotId || undefined }),
      });
      setMessage(`${result.recommendation.rec_id} ${result.created ? "added to" : "already exists in"} the recommendation inbox.`);
      window.dispatchEvent(new Event("bloodnet:recommendations-changed"));
    } catch (error) {
      setMessage(userFacingError(error, "The redistribution recommendation could not be created."));
    } finally { setCreating(""); }
  };
  const critical = briefing?.critical_nodes[0];
  const impact = briefing?.vulnerabilities.find((item) => item.unavailable_bank_id === critical?.bank_id);
  return <Panel eyebrow="Regional network intelligence" title="Supply dependency and resilience" className="wide-feature">
    <div className="network-intelligence-head"><p className="feature-note">Live topology combines supply history, current inventory, travel routes, donor catchments, and facility dependencies.</p><button className="text-button" onClick={() => void load(true)} disabled={status === "loading"}>{status === "loading" ? "Analyzing..." : "Refresh analysis"}</button></div>
    {status === "loading" && !briefing ? <EmptyFeature>Building the regional network snapshot...</EmptyFeature> : status === "unavailable" ? <EmptyFeature>Network intelligence is temporarily unavailable.</EmptyFeature> : status === "empty" ? <EmptyFeature>No operational relationships are available in this region yet.</EmptyFeature> : briefing && <>
      <div className="network-intelligence-metrics">
        <div><div className="network-metric-heading"><span className="network-metric-label">Critical supply node</span><CardInfo title="Critical supply node" description="The bank with the highest dependency score, calculated from dependent hospitals, served hospitals, historical supply volume, and transfer connectivity. This is decision support, not an automatic priority." /></div><strong>{critical?.bank_id || "—"}</strong><small>{critical?.region || "Region unavailable"} · dependency score {critical?.importance_score ?? 0}</small></div>
        <div className={briefing.summary.vulnerable_hospitals ? "has-risk" : ""}><div className="network-metric-heading"><span className="network-metric-label">Failure exposure</span><CardInfo title="Failure exposure" description="Hospitals without another stocked bank reachable inside the configured response-time window if a critical node becomes unavailable." /></div><strong>{briefing.summary.vulnerable_hospitals}</strong><small>vulnerable hospitals</small></div>
        <div><div className="network-metric-heading"><span className="network-metric-label">Relationships</span><CardInfo title="Network relationships" description="Current bank-to-hospital supply and bank-to-bank transfer relationships in the scoped regional snapshot." /></div><strong>{briefing.summary.relationships}</strong><small>{briefing.summary.banks} banks · {briefing.summary.hospitals} hospitals</small></div>
      </div>
      <div className="network-intelligence-detail">
        <section><div className="subsection-title"><strong>If {critical?.bank_id || "the critical bank"} becomes unavailable</strong><CardInfo title="Failure simulation" description="Removes the selected bank from the current topology and checks whether affected hospitals retain an in-window, stocked alternative." /></div>{impact?.affected_hospitals.length ? <div className="network-impact-list">{impact.affected_hospitals.slice(0, 6).map((hospital) => { const vulnerable = impact.vulnerable_hospitals.includes(hospital); const alternative = impact.alternatives[hospital]?.[0]; return <div key={hospital}><span className={`badge ${vulnerable ? "danger" : "success"}`}>{vulnerable ? "Vulnerable" : "Covered"}</span><strong>{hospital}</strong><small>{alternative ? `Alternative ${alternative.bank_id} · ${formatTravelTime(alternative.travel_time_hours)}` : "No stocked alternative within response window"}</small></div>; })}</div> : <EmptyFeature>No hospital dependency is attached to this node.</EmptyFeature>}</section>
        <section><div className="subsection-title"><strong>Pre-shortage balancing</strong><CardInfo title="Pre-shortage balancing" description="Suggests a route from surplus to exposed inventory while retaining the configured reserve. Every transfer remains a human-approved recommendation." /></div>{briefing.redistribution_opportunities.length ? <div className="network-transfer-list">{briefing.redistribution_opportunities.slice(0, 4).map((item) => { const key = `${item.from_bank}-${item.to_bank}-${item.blood_group}-${item.component}`; return <div key={key}><span>{item.blood_group} {item.component}</span><strong>{item.from_bank} → {item.to_bank}</strong><small>{item.suggested_units} units · approval required{item.travel_time_hours != null ? ` · ${formatTravelTime(item.travel_time_hours)}` : ""}</small>{canRecommend && <button className="text-button" disabled={Boolean(creating)} onClick={() => void createRecommendation(item)}>{creating === key ? "Adding..." : "Add to recommendations"}</button>}</div>; })}</div> : <EmptyFeature>No safe redistribution opportunity is indicated right now.</EmptyFeature>}{message && <p className="network-recommendation-message" role="status">{message}</p>}</section>
      </div>
      <p className="network-snapshot-note">{stale ? "Showing the last persisted snapshot" : "Snapshot persisted"}{snapshotId ? ` · ${snapshotId.slice(0, 8)}` : ""}. Analysis never executes a transfer.</p>
    </>}
  </Panel>;
}

function weatherRisk(probability: number) {
  if (probability >= 0.7) return "critical";
  if (probability >= 0.45) return "elevated";
  if (probability >= 0.2) return "watch";
  return "ok";
}

export function AdminFeaturePanels({ api, cases, recommendations, onDecision, role, region, section }: { api: Api; cases: CaseRecord[]; recommendations: Recommendation[]; onDecision: (id: string, decision: "approve" | "reject", rationale?: string) => Promise<DecisionResult>; role: "regional_admin" | "auditor"; region: string; section?: "recommendations" }) {
  const [question, setQuestion] = useState("Which cases are most exposed to a donor shortfall today?");
  const [chat, setChat] = useState<{ answer?: string; trace?: { tool?: string; result?: unknown }[] } | null>(null);
  const [chatError, setChatError] = useState("");
  const [chatBusy, setChatBusy] = useState(false);
  const [rejecting, setRejecting] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [selectedRecommendation, setSelectedRecommendation] = useState<Recommendation | null>(null);
  const [decisionResult, setDecisionResult] = useState<DecisionResult | null>(null);
  const [decisionResults, setDecisionResults] = useState<Record<string, DecisionResult>>({});
  const { points: forecast, isStale: forecastIsStale } = useForecast(api, region);
  const [health, setHealth] = useState<NetworkHealth | null>(null);
  const pending = recommendations.filter((item) => item.state === "AWAITING_APPROVAL");
  const [visiblePending, , pendingPagination] = usePagination(pending, 8);
    <Panel eyebrow="Human approval boundary" title="Recommendation inbox" className="wide-feature">{pending.length ? visiblePending.map((item) => <div className="feature-recommendation" key={item.rec_id}><div><strong>{item.type.replaceAll("_", " ")}</strong><small>{item.rec_id}</small></div><p>{item.rationale || "Evidence-backed intervention proposal."}</p><div className="actions"><button className="text-button" onClick={() => { setSelectedRecommendation(item); setDecisionResult(decisionResults[item.rec_id] || null); }}>{role === "auditor" ? "Inspect detail" : "View detail"}</button>{role !== "auditor" && <button className="primary compact" onClick={() => { setSelectedRecommendation(item); setDecisionResult(null); }}>Review and approve</button>}{decisionResults[item.rec_id] && <RecommendationResult result={decisionResults[item.rec_id]} />}</div>{role !== "auditor" && (rejecting === item.rec_id ? <div className="actions"><input value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} placeholder="Reason required" aria-label="Rejection reason" /><button className="secondary compact" disabled={!rejectReason.trim()} onClick={() => { void onDecision(item.rec_id, "reject", rejectReason.trim()); setRejecting(""); setRejectReason(""); }}>Confirm rejection</button><button className="text-button" onClick={() => { setRejecting(""); setRejectReason(""); }}>Cancel</button></div> : <button className="secondary compact" onClick={() => setRejecting(item.rec_id)}>Reject</button>)}</div>) : <EmptyFeature>No pending recommendations.</EmptyFeature>}{pendingPagination}<RecommendationDetailDialog recommendation={selectedRecommendation} result={decisionResult} open={selectedRecommendation !== null} onClose={() => { setSelectedRecommendation(null); setDecisionResult(null); }} onApprove={async () => { if (!selectedRecommendation) return; const result = await onDecision(selectedRecommendation.rec_id, "approve"); setDecisionResult(result); setDecisionResults((current) => ({ ...current, [selectedRecommendation.rec_id]: result })); setSelectedRecommendation(null); }} onReject={() => { if (selectedRecommendation) void onDecision(selectedRecommendation.rec_id, "reject"); setSelectedRecommendation(null); }} /></Panel>
  const chatSopPassages = (chat?.trace || []).flatMap((item) => {
    if (item.tool !== "search_sops" || !item.result || typeof item.result !== "object") return [];
    const data = (item.result as { data?: { passages?: SopPassage[] } }).data;
    return data?.passages || [];
  });
  const forecastDates = selectForecastDates(forecast);
  const forecastGroups = forecastBloodGroups(forecast);
  useEffect(() => { void api<NetworkHealth>("/match-svc/api/v1/network/health").then(setHealth).catch(() => setHealth(null)); }, [api]);
    const runChat = async () => { setChatBusy(true); setChatError(""); setChat(null); try { setChat(await api("/agent-svc/api/v1/investigations/gemini", { method: "POST", body: JSON.stringify({ question }) })); } catch (error) { setChatError(userFacingError(error, "The analyst could not complete that request. Please try again.")); } finally { setChatBusy(false); } };
  return <div className={`feature-grid admin-features ${section || ""}`}>
    <Panel eyebrow="Network health" title="Operational pulse"><div className="health-grid"><div><strong>{health?.active_cases ?? "--"}</strong><span>active cases</span></div><div><strong>{health?.fulfillment_probability == null ? "--" : `${Math.round(health.fulfillment_probability * 100)}%`}</strong><span>fulfillment probability</span></div><div><strong>{health?.shortage_exposure ?? "--"}</strong><span>shortage exposure</span></div></div><div className="health-line"><i />{health ? `${health.donor_activity} donors active · ${health.escalated_cases} escalated` : "Network health unavailable"}<b>{health?.status?.toUpperCase() || "UNKNOWN"}</b></div>{health && <div className="delivery-health"><span><strong>{health.notifications_delivered}</strong> delivered</span><span><strong>{health.notifications_pending}</strong> pending</span><span className={health.notifications_failed ? "has-failure" : ""}><strong>{health.notifications_failed}</strong> failed</span></div>}</Panel>
    <Panel eyebrow="Blood Weather" title="Shortage probability, next 7 days"><div className="weather-legend"><span><i className="risk-ok" />Healthy</span><span><i className="risk-watch" />Watch</span><span><i className="risk-elevated" />Elevated</span><span><i className="risk-critical" />Critical</span></div>{forecastDates.length ? <table className="weather-matrix"><thead><tr><th />{forecastDates.map((date, index) => <th key={date}>{forecastIsStale ? new Date(`${date}T00:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : index === 0 ? "Today" : `+${index}`}</th>)}</tr></thead><tbody>{forecastGroups.map((group) => <tr key={group}><th scope="row">{group}</th>{forecastDates.map((date) => { const probability = forecastCellRisk(forecast, group, date); const risk = probability == null ? "ok" : weatherRisk(probability); return <td key={`${group}-${date}`}><span className={`weather-cell risk-${risk}`} title={probability == null ? "No forecast data" : `${Math.round(probability * 100)}% shortage probability`}>{probability == null ? "--" : `${Math.round(probability * 100)}%`}</span></td>; })}</tr>)}</tbody></table> : <EmptyFeature>Forecast data is not available for this region.</EmptyFeature>}</Panel>
    <Panel eyebrow="Human approval boundary" title="Recommendation inbox" className="wide-feature">{pending.length ? pending.map((item) => <div className="feature-recommendation" key={item.rec_id}><div><strong>{item.type.replaceAll("_", " ")}</strong><small>{item.rec_id}</small></div><p>{item.rationale || "Evidence-backed intervention proposal."}</p><div className="actions"><button className="text-button" onClick={() => { setSelectedRecommendation(item); setDecisionResult(decisionResults[item.rec_id] || null); }}>{role === "auditor" ? "Inspect detail" : "View detail"}</button>{role !== "auditor" && <button className="primary compact" onClick={() => { setSelectedRecommendation(item); setDecisionResult(null); }}>Review and approve</button>}{decisionResults[item.rec_id] && <RecommendationResult result={decisionResults[item.rec_id]} />}</div>{role !== "auditor" && (rejecting === item.rec_id ? <div className="actions"><input value={rejectReason} onChange={(event) => setRejectReason(event.target.value)} placeholder="Reason required" aria-label="Rejection reason" /><button className="secondary compact" disabled={!rejectReason.trim()} onClick={() => { void onDecision(item.rec_id, "reject", rejectReason.trim()); setRejecting(""); setRejectReason(""); }}>Confirm rejection</button><button className="text-button" onClick={() => { setRejecting(""); setRejectReason(""); }}>Cancel</button></div> : <button className="secondary compact" onClick={() => setRejecting(item.rec_id)}>Reject</button>)}</div>) : <EmptyFeature>No pending recommendations.</EmptyFeature>}<RecommendationDetailDialog recommendation={selectedRecommendation} result={decisionResult} open={selectedRecommendation !== null} onClose={() => { setSelectedRecommendation(null); setDecisionResult(null); }} onApprove={async () => { if (!selectedRecommendation) return; const result = await onDecision(selectedRecommendation.rec_id, "approve"); setDecisionResult(result); setDecisionResults((current) => ({ ...current, [selectedRecommendation.rec_id]: result })); }} onReject={() => { if (selectedRecommendation) setRejecting(selectedRecommendation.rec_id); setSelectedRecommendation(null); }} canApprove={role !== "auditor"} /></Panel>
    <Panel eyebrow="Gemini / read-only analyst" title="Agent chat" optional><form className="agent-form" onSubmit={(event) => { event.preventDefault(); void runChat(); }}><textarea value={question} onChange={(event) => setQuestion(event.target.value)} /><button className="primary" disabled={chatBusy}>{chatBusy ? "Investigating..." : "Ask Gemini"}</button></form>{chatError && <div className="notice error agent-error" role="alert"><span>{chatError}</span><button className="text-button" onClick={() => void runChat()} disabled={chatBusy}>Try again</button></div>}{chat?.answer && <div className="agent-answer"><p>{chat.answer}</p>{chatSopPassages.length > 0 && <div className="agent-citations"><strong>Supporting SOP evidence</strong>{chatSopPassages.map((passage) => <span key={passage.citation}>{passage.citation}</span>)}</div>}</div>}</Panel>
    {section !== "recommendations" && <NetworkIntelligencePanel api={api} region={region} canRecommend={role !== "auditor"} />}
  </div>;
}

function useForecast(api: Api, region: string) { const [forecast, setForecast] = useState<{ points: ForecastPoint[]; isStale: boolean }>({ points: [], isStale: false }); useEffect(() => { if (!region) return; void api<{ forecast?: ForecastPoint[]; is_stale?: boolean }>(`/match-svc/api/v1/regional/forecast?region=${encodeURIComponent(region)}&horizon_days=7`).then((result) => setForecast({ points: result.forecast || [], isStale: Boolean(result.is_stale) })).catch(() => setForecast({ points: [], isStale: false })); }, [api, region]); return forecast; }

export function DonorFeaturePanels({ api }: { api: Api }) {
  const [consent, setConsent] = useState(true);
  const [history, setHistory] = useState<{ outreach_id: string; status: string; message: string }[]>([]);
  useEffect(() => { void api<{ notifications?: { outreach_id?: string; status: string; message?: string }[] }>("/match-svc/api/v1/notifications").then((result) => setHistory((result.notifications || []).map((item) => ({ outreach_id: item.outreach_id || "notification", status: item.status, message: item.message || "Donation opportunity" })))).catch(() => undefined); }, [api]);
  return <div className="feature-grid donor-features"><Panel eyebrow="Your privacy" title="Consent controls"><label className="toggle-line"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>Allow BloodNet to contact me for compatible requests</span></label><p className="feature-note">You can pause outreach at any time. Medical eligibility is checked before an opportunity is shown.</p></Panel><Panel eyebrow="Donor record" title="Donation history"><div className="history-list">{history.length ? history.map((item) => <div key={item.outreach_id}><strong>{item.message}</strong><span>{item.status}</span></div>) : <EmptyFeature>Your completed donations will appear here.</EmptyFeature>}</div></Panel></div>;
}

function BadgeLite({ children }: { children: React.ReactNode }) { return <span className="badge danger">{children}</span>; }

export function DonorPortal({ api, donorId }: { api: Api; donorId: string }) {
  const [responseError, setResponseError] = useState("");
  const [opportunities, setOpportunities] = useState<{ outreach_id: string; message: string; status: string; blood_group?: string; component?: string; quantity?: number; required_by?: string; hospital_id?: string }[]>([]);
  const [consent, setConsent] = useState(false);
  const [channels, setChannels] = useState<string[]>(["email"]);
  const [eligibility, setEligibility] = useState("eligible");
  const [nextEligibleAt, setNextEligibleAt] = useState("");
  const [donationHistory, setDonationHistory] = useState<{ date?: string; status?: string }[]>([]);
  const [profile, setProfile] = useState<{ display_name?: string; blood_group?: string; date_of_birth?: string | null; city?: string; availability?: string; consent_contact?: boolean; notification_channels?: string[]; eligibility_status?: string; next_eligible_at?: string | null; donation_history?: { date?: string; status?: string }[] }>({});
  const [busy, setBusy] = useState("");
  const [profileReady, setProfileReady] = useState(false);
  useEffect(() => { void api<{ opportunities?: { outreach_id: string; message: string; status: string; blood_group?: string; component?: string; quantity?: number; required_by?: string; hospital_id?: string }[] }>(`/swarm-svc/api/v1/opportunities?donor_id=${encodeURIComponent(donorId)}`).then((result) => setOpportunities((result.opportunities || []).map((item) => ({ ...item, message: `${item.message} · ${item.quantity || "-"} unit(s) ${item.component || ""} · required by ${item.required_by ? new Date(item.required_by).toLocaleString() : "time pending"} · travel estimate is calculated after location confirmation` })))).catch(() => setOpportunities([])); }, [api, donorId]);
  useEffect(() => { void api<typeof profile>("/match-svc/api/v1/auth/donor-profile").then((saved) => { setProfile(saved); setConsent(Boolean(saved.consent_contact)); setChannels(saved.notification_channels || ["email"]); setEligibility(saved.eligibility_status || "eligible"); setNextEligibleAt(saved.next_eligible_at || ""); setDonationHistory(saved.donation_history || []); setProfileReady(true); }).catch(() => setProfileReady(false)); }, [api, donorId]);
  const respond = async (opportunity: { outreach_id: string }, response: "accept" | "decline") => { setBusy(opportunity.outreach_id); setResponseError(""); try { const result = await api<{ status: string }>(`/swarm-svc/api/v1/outreach/${opportunity.outreach_id}/response`, { method: "POST", body: JSON.stringify({ donor_id: donorId, response }) }); setOpportunities((current) => current.map((item) => item.outreach_id === opportunity.outreach_id ? { ...item, status: result.status } : item)); } catch (error) { setResponseError(userFacingError(error, "Your response could not be saved. Please try again.")); } finally { setBusy(""); } };
  const completeDonation = async (outreachId?: string) => { setBusy(`complete:${outreachId || "donation"}`); try { const saved = await api<typeof profile>("/match-svc/api/v1/auth/donor-profile/donations/complete", { method: "POST", body: JSON.stringify({ outreach_id: outreachId }) }); setProfile(saved); setEligibility(saved.eligibility_status || "deferred"); setNextEligibleAt(saved.next_eligible_at || ""); setDonationHistory(saved.donation_history || []); } finally { setBusy(""); } };
  const updatePreference = async (enabled: boolean, nextChannels = ["email"]) => { if (!profileReady) return; setConsent(enabled); try { await api("/match-svc/api/v1/auth/donor-profile", { method: "PUT", body: JSON.stringify({ ...profile, consent_contact: enabled, notification_channels: nextChannels, date_of_birth: profile.date_of_birth || null }) }); } catch { setConsent(!enabled); } };
  const history = opportunities.filter((opportunity) => opportunity.status !== "pending");
  return <div className="feature-grid donor-features">{responseError && <p role="alert" className="notice">{responseError}</p>}<Panel eyebrow="Next opportunity" title="Request card" className="wide-feature">{opportunities.filter((opportunity) => opportunity.status === "pending").length ? opportunities.filter((opportunity) => opportunity.status === "pending").map((opportunity) => <div className="request-card" key={opportunity.outreach_id}><div><span className="blood-mark">+</span><div><BadgeLite>Compatible request</BadgeLite><h3>{opportunity.message}</h3><p>Response window is open · {opportunity.outreach_id}</p></div></div><div className="actions"><button className="primary" disabled={busy === opportunity.outreach_id} onClick={() => void respond(opportunity, "accept")}>Accept request</button><button className="secondary" disabled={busy === opportunity.outreach_id} onClick={() => void respond(opportunity, "decline")}>Decline</button></div></div>) : <EmptyFeature>No active donation opportunities.</EmptyFeature>}</Panel><Panel eyebrow="Eligibility" title="Donation readiness"><BadgeLite>{profileReady ? eligibility : "Not available"}</BadgeLite><p className="feature-note">{nextEligibleAt ? `Next eligibility review: ${new Date(nextEligibleAt).toLocaleDateString()}` : profileReady ? "Eligibility is checked before an opportunity is shown." : "Complete your donor profile to calculate readiness."}</p></Panel><Panel eyebrow="Your privacy" title="Consent controls"><label className="toggle-line"><input type="checkbox" checked={consent} disabled={!profileReady} onChange={(event) => void updatePreference(event.target.checked)} /><span>Allow compatible donation outreach</span></label><div className="channel-list">{["email"].map((channel) => <label key={channel}><input type="checkbox" checked={channels.includes(channel)} disabled={!profileReady} onChange={(event) => { const next = event.target.checked ? [...channels, channel] : channels.filter((item) => item !== channel); setChannels(next); void updatePreference(consent, next); }} /> {channel.toUpperCase()}</label>)}</div></Panel><Panel eyebrow="Your activity" title="Response history"><div className="history-list">{donationHistory.map((item, index) => <div key={`${item.date}-${index}`}><strong>{item.status || "Donation recorded"}</strong><span>{item.date || "-"}</span></div>)}{history.map((opportunity) => <div key={opportunity.outreach_id}><strong>{opportunity.message}</strong><span>{opportunity.status}</span></div>)}{!donationHistory.length && !history.length && <EmptyFeature>No donation activity recorded.</EmptyFeature>}</div></Panel></div>;
}

export function DonorOnboarding({ api }: { api: Api }) {
  const [profile, setProfile] = useState({ display_name: "", blood_group: "O+", date_of_birth: "", city: "", region_id: "", availability: "available", consent_contact: false, notification_channels: ["email"], eligibility_status: "eligible", last_donation_at: "", next_eligible_at: "", donation_history: [] as { date?: string; status?: string }[] });
  const [everDonated, setEverDonated] = useState(false);
  const [status, setStatus] = useState("");
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(true);

  useEffect(() => {
    void api<typeof profile>("/match-svc/api/v1/auth/donor-profile")
      .then((saved) => {
        const nextProfile = {
          ...saved,
          date_of_birth: saved.date_of_birth || "",
          last_donation_at: saved.last_donation_at || "",
          next_eligible_at: saved.next_eligible_at || "",
          notification_channels: saved.notification_channels || ["email"],
          donation_history: saved.donation_history || [],
          region_id: saved.region_id || saved.city || "",
        };
        setProfile((current) => ({ ...current, ...nextProfile }));
        setEverDonated(Boolean(nextProfile.last_donation_at && nextProfile.last_donation_at.trim()));
        setEditing(!(nextProfile.display_name && nextProfile.city && nextProfile.region_id));
      })
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [api]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setLoading(true);
    setStatus("");
    try {
      const { next_eligible_at: _ignoredNextEligibleAt, ...requestBody } = profile;
      const adultCutoff = new Date();
      adultCutoff.setUTCFullYear(adultCutoff.getUTCFullYear() - 18);
      if (!profile.date_of_birth || profile.date_of_birth > adultCutoff.toISOString().slice(0, 10)) {
        throw new Error("You must be at least 18 years old to create a donor profile.");
      }
      if (!profile.city.trim() || !profile.region_id.trim()) {
        throw new Error("Choose a donor location and service region before saving.");
      }
      const savedProfile = await api<typeof profile>("/match-svc/api/v1/auth/donor-profile", {
        method: "PUT",
        body: JSON.stringify({
          ...requestBody,
          city: profile.city.trim(),
          region_id: profile.region_id.trim(),
          date_of_birth: profile.date_of_birth || null,
          last_donation_at: profile.last_donation_at || null,
        }),
      });
      const nextProfile = {
        ...savedProfile,
        date_of_birth: savedProfile.date_of_birth || "",
        last_donation_at: savedProfile.last_donation_at || "",
        next_eligible_at: savedProfile.next_eligible_at || "",
        notification_channels: savedProfile.notification_channels || ["email"],
        donation_history: savedProfile.donation_history || [],
        region_id: savedProfile.region_id || profile.region_id || "",
      };
      setProfile(nextProfile);
      setEverDonated(Boolean(nextProfile.last_donation_at && nextProfile.last_donation_at.trim()));
      setEditing(false);
      setStatus("Donor profile saved.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Profile setup failed");
    } finally {
      setLoading(false);
    }
  };

  return <Panel eyebrow="Donor onboarding" title="Your donor profile" className="wide-feature">
    {editing ? <form className="agent-form" onSubmit={save}>
      <div className="structured-request">
        <label>Name<input value={profile.display_name} onChange={(event) => setProfile({ ...profile, display_name: event.target.value })} required /></label>
        <label>Blood group<select value={profile.blood_group} onChange={(event) => setProfile({ ...profile, blood_group: event.target.value })}>{["O+", "O-", "A+", "A-", "B+", "B-", "AB+", "AB-"].map((group) => <option key={group}>{group}</option>)}</select></label>
        <label>Date of birth<input type="date" value={profile.date_of_birth} onChange={(event) => setProfile({ ...profile, date_of_birth: event.target.value })} required /></label>
        <label>Have you donated before?
          <select value={everDonated ? "yes" : "no"} onChange={(event) => {
            const nextHasDonated = event.target.value === "yes";
            setEverDonated(nextHasDonated);
            setProfile({
              ...profile,
              last_donation_at: nextHasDonated ? (profile.last_donation_at || "") : "",
            });
          }}>
            <option value="no">No</option>
            <option value="yes">Yes</option>
          </select>
        </label>
        {everDonated && (
          <label>When was your last donation?<input type="datetime-local" value={profile.last_donation_at ? profile.last_donation_at.slice(0, 16) : ""} onChange={(event) => setProfile({ ...profile, last_donation_at: event.target.value })} required /></label>
        )}
        <label>City / location<select value={profile.city} onChange={(event) => setProfile({ ...profile, city: event.target.value })} required><option value="">Select an Indian city</option>{INDIAN_CITIES.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
        <label>Location preference / region<select value={profile.region_id} onChange={(event) => setProfile({ ...profile, region_id: event.target.value })} required><option value="">Select your service region</option>{INDIAN_CITIES.map((option) => <option key={option} value={option}>{option}</option>)}</select></label>
        <label>Availability<select value={profile.availability} onChange={(event) => setProfile({ ...profile, availability: event.target.value })}><option value="available">Available</option><option value="paused">Paused</option></select></label>
      </div>
      <p className="feature-note eligibility-calculation">Next eligible date: <strong>{profile.next_eligible_at ? new Date(profile.next_eligible_at).toLocaleDateString() : (everDonated ? "Calculated after your last donation date is saved" : "You are currently available to donate")}</strong></p>
      <label>Notification channels<div className="channel-list">{["email"].map((channel) => <label key={channel}><input type="checkbox" checked={profile.notification_channels.includes(channel)} onChange={(event) => setProfile({ ...profile, notification_channels: event.target.checked ? [...profile.notification_channels, channel] : profile.notification_channels.filter((item) => item !== channel) })} /> {channel.toUpperCase()}</label>)}</div></label>
      <label className="toggle-line"><input type="checkbox" checked={profile.consent_contact} onChange={(event) => setProfile({ ...profile, consent_contact: event.target.checked })} /><span>Allow compatible donation outreach</span></label>
      <button className="primary" disabled={loading}>{loading ? "Loading..." : "Save donor profile"}</button>
    </form> : <div className="saved-profile">
      <div className="saved-profile-header"><span className="badge neutral">Saved</span><button type="button" className="secondary compact" onClick={() => { setEditing(true); setStatus(""); }}>Edit profile</button></div>
      <div className="saved-profile-grid">
        <div><strong>Name</strong><span>{profile.display_name}</span></div>
        <div><strong>Blood group</strong><span>{profile.blood_group}</span></div>
        <div><strong>Date of birth</strong><span>{profile.date_of_birth || "Not provided"}</span></div>
        <div><strong>Last donation</strong><span>{profile.last_donation_at ? new Date(profile.last_donation_at).toLocaleDateString() : "Not recorded"}</span></div>
        <div><strong>Next eligible date</strong><span>{profile.next_eligible_at ? new Date(profile.next_eligible_at).toLocaleDateString() : "Not calculated"}</span></div>
        <div><strong>City</strong><span>{profile.city}</span></div>
        <div><strong>Location preference / region</strong><span>{profile.region_id || profile.city}</span></div>
        <div><strong>Availability</strong><span>{profile.availability}</span></div>
        <div><strong>Notification channels</strong><span>{profile.notification_channels.join(", ").toUpperCase()}</span></div>
      </div>
    </div>}
    {status && <div className="notice">{status}</div>}
  </Panel>;
}

export function HospitalFeaturePanels({ api, hospitalId, cases, onCaseUpdated }: { api: Api; hospitalId: string; cases: CaseRecord[]; onCaseUpdated?: () => Promise<void> }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("");
  const [draft, setDraft] = useState<Record<string, unknown> | null>(null);
  const [draftConfidence, setDraftConfidence] = useState(0);
  const [inventory, setInventory] = useState<{ unit_id: string; bank_id?: string; group: string; component: string; status: string }[]>([]);
  const [bloodGroup, setBloodGroup] = useState("O+");
  const [component, setComponent] = useState("RBC");
  const [quantity, setQuantity] = useState("2");
  const [urgency, setUrgency] = useState("High");
  const [requiredBy, setRequiredBy] = useState("");
  const [context, setContext] = useState<{ hospital: Record<string, unknown>; banks: Record<string, unknown>[]; units: typeof inventory } | null>(null);
  const [document, setDocument] = useState<File | null>(null);
  const [intakeMode, setIntakeMode] = useState<"manual" | "document" | "clinical">("manual");
  const [clinicalFormat, setClinicalFormat] = useState<"fhir" | "hl7v2">("fhir");
  const [sourceEventId, setSourceEventId] = useState("");
  const [clinicalPayload, setClinicalPayload] = useState("");

  useEffect(() => {
    void api<{ hospital: Record<string, unknown>; banks: Record<string, unknown>[]; units: typeof inventory }>("/match-svc/api/v1/hospital/context")
      .then((result) => {
        setContext(result);
        setInventory(result.units || []);
      })
      .catch((error) => {
        setContext(null);
        setInventory([]);
        setStatus(error instanceof Error ? error.message : "Hospital location and regional scope are unavailable.");
      });
  }, [api]);

  const extractManual = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setStatus("");

    const requestId = typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `REQ-${Date.now()}`;

    const manualRequest = {
      request_id: requestId,
      group: bloodGroup,
      component,
      qty: Number(quantity),
      urgency,
      required_by: requiredBy ? new Date(requiredBy).toISOString() : "",
      hospital_id: hospitalId,
      source_channel: "hospital-portal",
    };

    try {
      if (!text.trim()) {
        setDraftConfidence(1);
        setDraft(manualRequest);
        setStatus("Review the request fields before creating a case.");
        return;
      }

      const result = await api<{ request?: Record<string, unknown>; confidence?: number }>("/intake-svc/api/v1/extract", {
        method: "POST",
        body: JSON.stringify({ raw_text: text, hospital_id: hospitalId, source_channel: "hospital-portal" }),
      });
      setDraftConfidence(result.confidence ?? 0);
      const extractedRequest = result.request ?? {};
      setDraft(extractedRequest ? {
        ...extractedRequest,
        group: bloodGroup,
        component,
        qty: Number(quantity),
        urgency,
        required_by: requiredBy ? new Date(requiredBy).toISOString() : extractedRequest.required_by,
      } : null);
      setStatus(result.request ? "Review the extracted fields before creating a case." : "The intake service returned no request draft.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Request extraction failed");
    } finally {
      setBusy(false);
    }
  };

  const submitDocument = async (event: FormEvent) => {
    event.preventDefault();
    if (!document) return;
    setBusy(true);
    setStatus("");
    try {
      const form = new FormData();
      form.append("file", document);
      form.append("hospital_id", hospitalId);
      form.append("source_channel", "hospital-document");
      form.append("provider", "gemini");
      const result = await api<{ request?: Record<string, unknown>; confidence?: number }> ("/intake-svc/api/v1/extract/document", {
        method: "POST",
        body: form,
      });
      setDraftConfidence(result.confidence ?? 0);
      setDraft(result.request ? { ...result.request, group: result.request.group || bloodGroup, component: result.request.component || component, qty: result.request.qty || Number(quantity), urgency: result.request.urgency || urgency, required_by: requiredBy ? new Date(requiredBy).toISOString() : result.request.required_by } : null);
      setStatus(result.request ? "Review the extracted fields before creating a case." : "Document intake returned no request draft.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Document intake failed");
    } finally {
      setBusy(false);
    }
  };

  const submitClinical = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setStatus("");
    try {
      const normalizedPayload = clinicalFormat === "fhir" ? JSON.parse(clinicalPayload) : clinicalPayload;
      const result = await api<{ request?: Record<string, unknown>; confidence?: number }>("/api/v1/clinical/requests/preview", {
        method: "POST",
        body: JSON.stringify({ format: clinicalFormat, payload: normalizedPayload, hospital_id: hospitalId, source_event_id: sourceEventId, provider: "gemini" }),
      });
      setDraftConfidence(result.confidence ?? 0);
      setDraft(result.request ? { ...result.request, source_event_id: sourceEventId } : null);
      setStatus(result.request ? "Review the extracted clinical fields before creating a case." : "Clinical intake returned no request draft.");
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Clinical intake failed");
    } finally {
      setBusy(false);
    }
  };

  const confirmDraft = async () => {
    if (!draft) return;
    const requiredFields = [draft.group, draft.component, draft.qty, draft.urgency, draft.required_by, draft.hospital_id];
    if (requiredFields.some((value) => value === undefined || value === null || value === "")) {
      setStatus("Complete the required request fields before creating a case.");
      return;
    }
    setBusy(true);
    setStatus(draftConfidence < 0.6 ? "Low-confidence extraction detected; creating case with the provided request fields." : "");
    if (!context) {
      setStatus("Hospital, bank, or location data is unavailable.");
      setBusy(false);
      return;
    }

    const region = typeof context.hospital.region === "string" ? context.hospital.region : "";
    if (!region) {
      setStatus("This hospital has no regional scope. Ask a regional administrator to configure it before creating a proximity-based request.");
      setBusy(false);
      return;
    }

    const group = String(draft.group || bloodGroup);
    const selectedComponent = String(draft.component || component);
    const unitsPayload = context.units
      .filter((unit) => unit.group === group && unit.component === selectedComponent && unit.status === "available")
      .slice(0, 12)
      .map((unit) => unit);

    try {
      await api("/match-svc/api/v1/match", {
        method: "POST",
        body: JSON.stringify({
          request: { ...draft, hospital_id: hospitalId, group, component: selectedComponent, region },
          hospital: context.hospital,
          banks: context.banks,
          units: unitsPayload,
          donors: [],
          eligible_donor_ids: [],
          eligibility_records: {},
        }),
      });
      setStatus("Request verified and case created.");
      setDraft(null);
      await onCaseUpdated?.();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : "Case creation failed");
    } finally {
      setBusy(false);
    }
  };

  const activeCases = cases.filter((item) => !["fulfilled", "cancelled", "unfulfilled"].includes(item.case.outcome || ""));

  return (
    <div className="feature-grid hospital-features">
      <Panel eyebrow="Hospital portal" title="Submit request" className="wide-feature">
        <div className="feature-toggle">
          <button type="button" className={intakeMode === "manual" ? "primary compact" : "secondary compact"} onClick={() => setIntakeMode("manual")} disabled={busy}>
            Manual request
          </button>
          <button type="button" className={intakeMode === "document" ? "primary compact" : "secondary compact"} onClick={() => setIntakeMode("document")} disabled={busy}>
            Upload order
          </button>
          <button type="button" className={intakeMode === "clinical" ? "primary compact" : "secondary compact"} onClick={() => setIntakeMode("clinical")} disabled={busy}>
            Ingest FHIR or HL7
          </button>
        </div>

        {intakeMode === "manual" && (
          <form className="agent-form" onSubmit={extractManual}>
            <div className="structured-request">
              <label>
                Blood group
                <select value={bloodGroup} onChange={(event) => setBloodGroup(event.target.value)} required>
                  <option>O+</option>
                  <option>O-</option>
                  <option>A+</option>
                  <option>A-</option>
                  <option>B+</option>
                  <option>B-</option>
                  <option>AB+</option>
                  <option>AB-</option>
                </select>
              </label>
              <label>
                Component
                <select value={component} onChange={(event) => setComponent(event.target.value)} required>
                  <option value="RBC">RBC</option>
                  <option value="Whole Blood">Whole Blood</option>
                  <option value="Platelets (RDP)">Platelets (RDP)</option>
                  <option value="Platelets (SDP)">Platelets (SDP)</option>
                  <option value="FFP">FFP</option>
                  <option value="Cryoprecipitate">Cryoprecipitate</option>
                </select>
              </label>
              <label>
                Quantity
                <input type="number" min="1" value={quantity} onChange={(event) => setQuantity(event.target.value)} required />
              </label>
              <label>
                Urgency
                <select value={urgency} onChange={(event) => setUrgency(event.target.value)} required>
                  <option value="Critical">Critical</option>
                  <option value="High">High</option>
                  <option value="Routine">Routine</option>
                </select>
              </label>
              <label>
                Required by
                <input type="datetime-local" value={requiredBy} onChange={(event) => setRequiredBy(event.target.value)} required />
              </label>
            </div>
            <label>
              Emergency details
              <textarea value={text} onChange={(event) => setText(event.target.value)} />
            </label>
            <button className="primary" disabled={busy}>
              {busy ? "Working..." : "Extract and review"}
            </button>
          </form>
        )}

        {intakeMode === "document" && (
          <form className="agent-form" onSubmit={submitDocument}>
            <p className="feature-note">Upload a PDF or text order and let the intake service draft the request fields.</p>
            <input type="file" accept="application/pdf,text/plain" onChange={(event) => setDocument(event.target.files?.[0] || null)} required />
            <button className="secondary" disabled={busy || !document}>
              {busy ? "Extracting..." : "Extract PDF or text order"}
            </button>
          </form>
        )}

        {intakeMode === "clinical" && (
          <form className="agent-form" onSubmit={submitClinical}>
            <p className="feature-note">Paste a provider-issued ServiceRequest or HL7v2 order. Patient identifiers are not retained by this adapter.</p>
            <div className="structured-request">
              <label>
                Format
                <select value={clinicalFormat} onChange={(event) => setClinicalFormat(event.target.value as "fhir" | "hl7v2")}>
                  <option value="fhir">FHIR ServiceRequest</option>
                  <option value="hl7v2">HL7v2</option>
                </select>
              </label>
              <label>
                Source event ID
                <input value={sourceEventId} onChange={(event) => setSourceEventId(event.target.value)} maxLength={128} required />
              </label>
            </div>
            <textarea
              value={clinicalPayload}
              onChange={(event) => setClinicalPayload(event.target.value)}
              placeholder={clinicalFormat === "fhir"
                ? '{"resourceType":"ServiceRequest","code":{"text":"O positive RBC"},"quantityQuantity":{"value":2,"unit":"units"}}'
                : "MSH|^~\\&\\rORC|NW|123\\rOBR|1|123|456|O positive RBC|STAT"}
              required
            />
            <button className="secondary" disabled={busy}>
              {busy ? "Extracting..." : "Extract and review"}
            </button>
          </form>
        )}

        {draft && (
          <div className="request-review">
            <p className="eyebrow">Verification required</p>
            {Object.entries(draft)
              .filter(([key]) => ["group", "component", "qty", "urgency", "required_by", "hospital_id"].includes(key))
              .map(([key, value]) => (
                <div className="hospital-case" key={key}>
                  <strong>{key.replaceAll("_", " ")}</strong>
                  <span>{String(value)}</span>
                </div>
              ))}
            <div className="actions">
              <button className="primary compact" onClick={() => void confirmDraft()} disabled={busy}>
                Confirm and create case
              </button>
              <button className="secondary compact" onClick={() => setDraft(null)} disabled={busy}>
                Edit request
              </button>
            </div>
          </div>
        )}

        {status && <div className="notice">{status}</div>}
      </Panel>

      {activeCases.length > 0 && (
        <Panel eyebrow="Hospital portal" title="Active cases" className="wide-feature">
          <div className="history-list">
            {activeCases.map((item) => (
              <div className="active-case" key={item.case.case_id}>
                <strong>{item.case.case_id}</strong>
                <HospitalReceiptForm api={api} item={item} onUpdated={onCaseUpdated} />
                <span>{item.request?.group || "-"} {item.request?.component || "-"} · {item.case.units_from_inventory || 0} units available</span>
              </div>
            ))}
          </div>
        </Panel>
      )}
    </div>
  );
}
export function HospitalJourneyEvidence({ api, cases }: { api: Api; cases: CaseRecord[] }) {
  const [events, setEvents] = useState<MessagingEvent[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState(cases[0]?.case.case_id || "");
  useEffect(() => { void api<{ events: MessagingEvent[] }>("/api/v1/inbound/messaging/events?limit=12").then((result) => setEvents(result.events)).catch(() => setEvents([])); }, [api, cases.length]);
  useEffect(() => { if (!selectedCaseId && cases[0]) setSelectedCaseId(cases[0].case.case_id); }, [cases, selectedCaseId]);
  const selected = cases.find((item) => item.case.case_id === selectedCaseId) || cases[0];
  const selectedEvent = events.find((event) => event.request_id === selected?.case.request_id);
  const hasCases = cases.length > 0;
  return <Panel eyebrow="Request-to-fulfillment evidence" title="Request source and outreach" className="wide-feature">
    <div className="journey-evidence-grid">
      <div className="journey-evidence-context"><label>Case<select value={selected?.case.case_id || ""} onChange={(event) => setSelectedCaseId(event.target.value)} disabled={!hasCases}>{hasCases ? cases.map((item) => <option key={item.case.case_id} value={item.case.case_id}>{item.case.case_id} · {item.request?.group || "-"} {item.request?.component || ""}</option>) : <option value="">No case available</option>}</select></label>{selected ? <><div className="journey-source"><span className="badge neutral">{selectedEvent?.provider || selected.request_ingress?.provider || selected.request?.source_channel || "hospital portal"}</span><div><strong>{selected.case.request_id}</strong><small>{selectedEvent ? `Signed provider event · ${selectedEvent.status}` : "Authenticated request intake"}</small></div><CardInfo title="Request source" description="Identifies how this request entered BloodNet. Signed provider events are deduplicated by provider event ID; sender details and raw message content remain hidden." /></div><div className="journey-delivery"><strong>Supply resilience</strong><span>{selected.supply_resilience?.single_supplier_dependency ? "Single stocked supplier dependency" : `${selected.supply_resilience?.alternative_suppliers || 0} stocked alternative(s)`}</span>{selected.supply_resilience?.single_supplier_dependency && <b className="badge danger">At risk</b>}<CardInfo title="Supply resilience" description="Shows whether this case depends on one stocked bank or has alternatives. Unit-level and unrelated regional inventory remain hidden." /></div><div className="journey-delivery"><strong>Donor outreach</strong><span>{selected.delivery_summary?.total ? `${selected.delivery_summary.delivered} of ${selected.delivery_summary.total} delivered` : "Starts after approved mobilization"}</span>{Boolean(selected.delivery_summary?.failed) && <b className="badge danger">{selected.delivery_summary?.failed} failed</b>}<CardInfo title="Donor outreach delivery" description="Aggregates durable outbox and provider receipt state for this case without exposing donor identifiers. Retries happen automatically within the configured limit." /></div></> : <EmptyFeature>No case is available for evidence review.</EmptyFeature>}</div>
      {selected && <InlineSopEvidence api={api} query={`${selected.request?.group || "blood"} ${selected.request?.component || "component"} ${selected.request?.urgency || "request"} allocation release and donor mobilization guidance`} />}
    </div>
  </Panel>;
}

export function GraphExplorer({ api }: { api: Api }) {
  const [analysisType, setAnalysisType] = useState("weak_coverage_zones");
  const [result, setResult] = useState<{ data?: unknown; nodes?: Array<{ id: string; type: string; label: string }>; edges?: Array<{ source: string; target: string }>; error?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [selectedNode, setSelectedNode] = useState<{ type: string; id: string } | null>(null);

  const runAnalysis = async () => {
    setBusy(true);
    try {
      const dataset = await api<GraphDataset>("/match-svc/api/v1/graph/dataset");
      const analysis = await api<{ data?: unknown; nodes?: Array<{ id: string; type: string; label: string }>; edges?: Array<{ source: string; target: string }> }>("/graph-svc/api/v1/graph/analysis", {
        method: "POST",
        body: JSON.stringify({ analysis_type: analysisType, demand_by_region: dataset.demand_by_region, supply_edges: dataset.supply_edges, donor_edges: dataset.donor_edges }),
      });
      setResult(analysis || {});
    } catch (error) {
      setResult({ error: error instanceof Error ? error.message : "Graph analysis failed" });
    } finally {
      setBusy(false);
    }
  };

  const errorMessage = result && typeof result === "object" && "error" in result ? String((result as Record<string, unknown>).error) : null;

  return (
    <div className="feature-grid graph-explorer">
      <Panel eyebrow="Network structure" title="Graph analysis">
        <div className="graph-controls">
          <select value={analysisType} onChange={(event) => setAnalysisType(event.target.value)}>
            <option value="weak_coverage_zones">Weak coverage zones</option>
            <option value="coverage_zones">Coverage zones</option>
            <option value="supplier_concentration">Supplier concentration</option>
            <option value="load_bearing_donors">Load-bearing donors</option>
            <option value="network_resilience">Network resilience</option>
            <option value="regional_demand_supply">Regional demand/supply</option>
          </select>
          <button className="secondary" onClick={() => void runAnalysis()} disabled={busy}>
            {busy ? "Analyzing..." : "Run analysis"}
          </button>
        </div>
      </Panel>

      {result && errorMessage && <Panel eyebrow="Results" title="Analysis output" className="wide-feature"><EmptyFeature>{errorMessage}</EmptyFeature></Panel>}

      {result && !errorMessage ? (
        <Panel eyebrow="Results" title="Analysis output" className="wide-feature">
          {result.nodes?.length ? <div className="graph-map" aria-label="Network graph preview">{result.nodes.slice(0, 12).map((node, index) => <button className={`graph-node graph-node-${index % 4}`} key={node.id} onClick={() => setSelectedNode(node)}><strong>{node.label || node.id}</strong><small>{node.type}</small></button>)}</div> : <EmptyFeature>No graph entities were returned for this analysis.</EmptyFeature>}
          {result.edges?.length ? <p className="feature-note graph-edge-summary">{result.edges.length} relationships connect the returned entities. Select a node to inspect its identifier and type.</p> : null}
          <div className="graph-result">
            <pre>{JSON.stringify((result as Record<string, unknown>).data, null, 2)}</pre>
          </div>
        </Panel>
      ) : null}

      {result && result.nodes && result.nodes.length > 0 && (
        <Panel eyebrow="Network nodes" title="Entities">
          <div className="node-list">
            {result.nodes.slice(0, 20).map((node) => (
              <div key={node.id} className={`node-item ${selectedNode?.id === node.id ? "selected" : ""}`} onClick={() => setSelectedNode(node)}>
                <strong>{node.label || node.id}</strong>
                <span>{node.type}</span>
              </div>
            ))}
          </div>
        </Panel>
      )}

      {selectedNode && (
        <Panel eyebrow="Node detail" title={selectedNode.id} className="wide-feature">
          <p className="feature-note">Type: {selectedNode.type}</p>
          <p className="feature-note">ID: {selectedNode.id}</p>
          <button className="secondary compact" onClick={() => setSelectedNode(null)}>
            Clear selection
          </button>
        </Panel>
      )}
    </div>
  );
}

export function AuditInvestigator({ api }: { api: Api }) {
  const [searchType, setSearchType] = useState("case_id");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<{ event_id: string; event_type: string; timestamp: string; actor?: string; payload?: Record<string, unknown> }[]>([]);
  const [selectedEvent, setSelectedEvent] = useState<{ event_id: string; event_type: string; timestamp: string; actor?: string; payload?: Record<string, unknown> } | null>(null);
  const [busy, setBusy] = useState(false);
  const [hasSearched, setHasSearched] = useState(false);
  const [provenance, setProvenance] = useState<Recommendation | null>(null);

  const search = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setHasSearched(true);
    try {
      const response = await api<{ events?: Array<{ event_id: string; event_type: string; timestamp: string; actor?: string; payload?: Record<string, unknown> }> }>("/match-svc/api/v1/audit/search", {
        method: "POST",
        body: JSON.stringify({ search_type: searchType, query }),
      });
      setResults(response.events || []);
    } catch (error) {
      setResults([{ event_id: "error", event_type: "error", timestamp: new Date().toISOString(), payload: { error: error instanceof Error ? error.message : "Search failed" } }]);
    } finally {
      setBusy(false);
    }
  };
  const showProvenance = async (event: React.MouseEvent, recommendationId: string) => {
    event.stopPropagation();
    const response = await api<{ recommendations?: Recommendation[] }>("/match-svc/api/v1/recommendations");
    setProvenance((response.recommendations || []).find((item) => item.rec_id === recommendationId) || null);
  };
  const exportResults = () => {
    const payload = results.map((item) => ({ event_id: item.event_id, event_type: item.event_type, timestamp: item.timestamp, actor: item.actor || "system" }));
    const csv = ["event_id,event_type,timestamp,actor", ...payload.map((item) => [item.event_id, item.event_type, item.timestamp, item.actor].map((value) => `"${String(value).replaceAll('"', '""')}"`).join(","))].join("\n");
    const url = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
    const link = document.createElement("a"); link.href = url; link.download = "bloodnet-audit-events.csv"; link.click(); URL.revokeObjectURL(url);
  };

  return (
    <div className="feature-grid audit-investigator">
      <Panel eyebrow="Audit search" title="Find events" className="wide-feature audit-search-panel">
        <p className="feature-note">Look up a case, request, recommendation, actor, or event type. Search results are read-only.</p>
        <form className="agent-form" onSubmit={search}>
          <select value={searchType} onChange={(event) => setSearchType(event.target.value)}>
            <option value="case_id">Case ID</option>
            <option value="request_id">Request ID</option>
            <option value="recommendation_id">Recommendation ID</option>
            <option value="actor">Actor</option>
            <option value="event_type">Event type</option>
            <option value="delivery_status">Delivery status</option>
            <option value="citation">Evidence citation</option>
          </select>
          <input type="text" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search query" />
          <button className="primary" disabled={busy}>
            {busy ? "Searching..." : "Search audit trail"}
          </button>
        </form>
      </Panel>

      {results.length > 0 && (
        <Panel eyebrow="Audit events" title={`Found ${results.length} event(s)`} className="wide-feature">
          <div className="audit-toolbar"><span className="feature-note">Read-only event timeline</span><button className="secondary compact" onClick={exportResults}>Export CSV</button></div>
          <div className="audit-timeline">
            {results.slice(0, 50).map((auditEvent, index) => (
              <div key={`${auditEvent.event_id}-${index}`} className={`audit-event ${selectedEvent?.event_id === auditEvent.event_id ? "selected" : ""}`} onClick={() => setSelectedEvent(auditEvent)}>
                <div className="event-header">
                  <strong>{auditEvent.event_type}</strong>
                  <span>{new Date(auditEvent.timestamp).toLocaleTimeString()}</span>
                </div>
                <small>{auditEvent.actor || "system"}</small>
                {Boolean(auditEvent.payload?.recommendation_id || auditEvent.payload?.rec_id) && <button className="text-button" onClick={(event) => void showProvenance(event, String(auditEvent.payload?.recommendation_id || auditEvent.payload?.rec_id))}>View recommendation provenance</button>}
              </div>
            ))}
          </div>
        </Panel>
      )}

      {hasSearched && !busy && results.length === 0 && (
        <Panel eyebrow="Audit events" title="No matching events" className="wide-feature audit-search-empty">
          <p className="feature-note">Try the exact identifier or select a different search field. Events appear after the related operation has completed.</p>
        </Panel>
      )}

      {selectedEvent && (
        <Panel eyebrow="Event detail" title={selectedEvent.event_type} className="wide-feature">
          <div className="audit-detail">
            <div>
              <strong>Event ID</strong>
              <span>{selectedEvent.event_id}</span>
            </div>
            <div>
              <strong>Type</strong>
              <span>{selectedEvent.event_type}</span>
            </div>
            <div>
              <strong>Timestamp</strong>
              <span>{new Date(selectedEvent.timestamp).toLocaleString()}</span>
            </div>
            {selectedEvent.actor && (
              <div>
                <strong>Actor</strong>
                <span>{selectedEvent.actor}</span>
              </div>
            )}
            {selectedEvent.payload && (
              <div>
                <strong>Payload</strong>
                <code>{JSON.stringify(selectedEvent.payload, null, 2)}</code>
              </div>
            )}
          </div>
          <button className="secondary compact" onClick={() => setSelectedEvent(null)}>
            Close detail
          </button>
        </Panel>
      )}

      {provenance && (
        <Panel eyebrow="Recommendation provenance" title={provenance.rec_id} className="wide-feature">
          <div className="audit-detail">
            <div><strong>Problem</strong><span>{provenance.rationale || provenance.type}</span></div>
            <div><strong>Evidence</strong><span>{provenance.evidence?.length ? provenance.evidence.map((item) => String(item.label || item.source || item.type || "Evidence")).join(" · ") : "No evidence recorded"}</span></div>
            <div><strong>Investigation</strong><span>{String(provenance.provenance?.investigation_id || provenance.provenance?.investigationId || "Deterministic workflow")}</span></div>
            <div><strong>Human decision</strong><span>{provenance.state === "AWAITING_APPROVAL" ? "Awaiting approval" : String(provenance.provenance?.approved_by || provenance.provenance?.approvedBy || "Recorded in approval history")}</span></div>
            <div><strong>Execution</strong><span>{provenance.state === "EXECUTED" || provenance.state === "PARTIAL" ? "Deterministic workflow executed" : provenance.state}</span></div>
            <div><strong>Outcome</strong><span>{String(provenance.expected_impact?.outcome || provenance.provenance?.outcome || "See linked case outcome")}</span></div>
            <div><strong>Final state</strong><span>{provenance.case_id ? `${provenance.case_id} · ${provenance.state}` : provenance.state}</span></div>
          </div>
          <button className="secondary compact" onClick={() => setProvenance(null)}>Close provenance</button>
        </Panel>
      )}
    </div>
  );
}

export function OptionalIntegrationsPanel({ api, role }: { api: Api; role: IntegrationRole }) {
  const [sopStatus, setSopStatus] = useState<SopStatus | null>(null);
  const [sopQuery, setSopQuery] = useState("emergency blood release and inventory allocation");
  const [sopResults, setSopResults] = useState<SopPassage[]>([]);
  const [sopBusy, setSopBusy] = useState(false);
  const [sopMessage, setSopMessage] = useState("");
  const [inboundEvents, setInboundEvents] = useState<MessagingEvent[]>([]);
  const [deliveries, setDeliveries] = useState<DeliveryOperation[]>([]);
  const canViewInbound = role === "hospital_coordinator" || role === "regional_admin" || role === "auditor";
  const canOperateDelivery = role === "regional_admin" || role === "auditor";
  const refreshOperations = () => {
    if (canViewInbound) void api<{ events: MessagingEvent[] }>("/api/v1/inbound/messaging/events?limit=25").then((result) => setInboundEvents(result.events)).catch(() => setInboundEvents([]));
    if (canOperateDelivery) void api<{ deliveries: DeliveryOperation[] }>("/match-svc/api/v1/notifications/delivery-operations?limit=50").then((result) => setDeliveries(result.deliveries)).catch(() => setDeliveries([]));
  };
  useEffect(() => {
    void api<SopStatus>("/agent-svc/api/v1/sops/status").then(setSopStatus).catch(() => setSopStatus(null));
    refreshOperations();
  }, [api, role]);
  const searchSops = async (event: FormEvent) => {
    event.preventDefault();
    setSopBusy(true); setSopMessage(""); setSopResults([]);
    try {
      const result = await api<{ passages: SopPassage[] }>("/agent-svc/api/v1/sops/search", { method: "POST", body: JSON.stringify({ query: sopQuery }) });
      setSopResults(result.passages);
      if (!result.passages.length) setSopMessage("No approved SOP passages matched this query.");
    } catch (reason) {
      setSopMessage(userFacingError(reason, "SOP evidence search failed."));
    } finally { setSopBusy(false); }
  };
  return <div className="integration-workspace">
    <div className="feature-grid">
      <Panel eyebrow="RAG / read-only" title="SOP evidence explorer" className="wide-feature" optional>
        <div className="integration-summary"><span><strong>{sopStatus?.document_count ?? 0}</strong> approved documents</span><span><strong>{sopStatus?.embedded_count ?? 0}</strong> vectorized</span><span><strong>{sopStatus?.citations_required ? "Required" : "Unknown"}</strong> citations</span><b className={`badge ${sopStatus?.ready ? "success" : "neutral"}`}>{sopStatus?.ready ? "Corpus ready" : "Bootstrap required"}</b></div>
        <form className="agent-form sop-search-form" onSubmit={searchSops}><label>Evidence question<input value={sopQuery} onChange={(event) => setSopQuery(event.target.value)} minLength={2} maxLength={500} required /></label><button className="secondary" disabled={sopBusy || !sopStatus?.ready}>{sopBusy ? "Searching..." : "Search approved SOPs"}</button></form>
        {sopMessage && <div className="notice">{sopMessage}</div>}
        <div className="evidence-results">{sopResults.map((passage) => <article key={`${passage.document_id}-${passage.citation}`}><div><strong>{passage.title}</strong><span className="badge success">Cited</span></div><p>{passage.content}</p><cite>{passage.citation}</cite>{typeof passage.similarity === "number" && <small>{Math.round(passage.similarity * 100)}% vector similarity</small>}</article>)}</div>
      </Panel>
      {canViewInbound && <Panel eyebrow="Provider ingress" title="Inbound channel activity" className={canOperateDelivery ? "" : "wide-feature"} optional><div className="panel-toolbar"><p className="feature-note">Signed provider events only. Sender and message content are never shown.</p><button className="text-button" onClick={refreshOperations}>Refresh</button></div>{inboundEvents.length ? <div className="operations-list">{inboundEvents.map((item) => <div key={`${item.provider}-${item.provider_event_id}`}><span className={`channel-chip ${item.provider}`}>{item.provider}</span><p><strong>{item.request_id}</strong><small>{item.hospital_id} · {new Date(item.received_at).toLocaleString()}</small></p><b className={`badge ${item.status === "ingested" ? "success" : "neutral"}`}>{item.status}</b></div>)}</div> : <EmptyFeature>No inbound provider events are visible in your scope.</EmptyFeature>}</Panel>}
      {canOperateDelivery && <Panel eyebrow="Durable messaging" title="Delivery operations" optional><div className="panel-toolbar"><p className="feature-note">Outbox, retry, and provider receipt state.</p><button className="text-button" onClick={refreshOperations}>Refresh</button></div>{deliveries.length ? <div className="operations-list">{deliveries.map((item) => <div key={item.event_id}><span className="attempt-count">{item.attempts}</span><p><strong>{item.delivery_status || item.status}</strong><small>{item.request_id} · attempt{item.attempts === 1 ? "" : "s"} {item.attempts}{item.next_retry_at ? ` · retry ${new Date(item.next_retry_at).toLocaleString()}` : ""}</small></p><b className={`badge ${item.delivery_status === "delivered" ? "success" : item.terminal_failure ? "danger" : "neutral"}`}>{item.terminal_failure ? "Terminal failure" : item.status}</b></div>)}</div> : <EmptyFeature>No notification deliveries are visible in your scope.</EmptyFeature>}</Panel>}
    </div>
  </div>;
}


function HospitalReceiptForm({ api, item, onUpdated }: { api: Api; item: CaseRecord; onUpdated?: () => void | Promise<void> }) {
  const [inventory, setInventory] = useState(String(item.case.confirmed_inventory_units || 0));
  const [donors, setDonors] = useState(String(item.case.confirmed_donor_units || 0));
  const [reference, setReference] = useState("");
  const [eventId, setEventId] = useState(() => crypto.randomUUID());
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const confirmed = (item.case.confirmed_inventory_units || 0) + (item.case.confirmed_donor_units || 0);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setBusy(true); setMessage("");
    try {
      await api(`/match-svc/api/v1/cases/${item.case.case_id}/outcomes`, { method: "POST", body: JSON.stringify({ event_id: eventId, inventory_units_received: Number(inventory), donor_units_received: Number(donors), reference }) });
      setMessage("Receipt recorded."); setEventId(crypto.randomUUID()); await onUpdated?.();
    } catch (error) { setMessage(error instanceof Error ? error.message : "Receipt could not be recorded."); }
    finally { setBusy(false); }
  };
  const close = async () => {
    setBusy(true);
    try { await api(`/match-svc/api/v1/cases/${item.case.case_id}/fulfill`, { method: "POST" }); await onUpdated?.(); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Case could not be closed."); }
    finally { setBusy(false); }
  };
  return <form onSubmit={submit} className="agent-form receipt-form">
    <p>{confirmed} of {item.request?.qty || 0} units confirmed received. Record cumulative units physically received at the hospital.</p>
    <label>Inventory units received<input type="number" min="0" max={item.request?.qty || 1000} step="1" value={inventory} onChange={e => { setInventory(e.target.value); setEventId(crypto.randomUUID()); }} required /></label>
    <label>Donor units received<input type="number" min="0" max={item.request?.qty || 1000} step="1" value={donors} onChange={e => { setDonors(e.target.value); setEventId(crypto.randomUUID()); }} required /></label>
    <label>Receipt reference<input value={reference} minLength={3} maxLength={200} onChange={e => { setReference(e.target.value); setEventId(crypto.randomUUID()); }} required /></label>
    <button type="submit" className="primary compact" disabled={busy}>Record received units</button>
    <button type="button" className="secondary compact" disabled={busy || confirmed < (item.request?.qty || 1)} onClick={() => void close()}>Confirm fulfillment and close</button>
    {message && <p role="status">{message}</p>}
  </form>;
}


export function FacilityLocationEditor({ api, savedLocation }: { api: Api; savedLocation?: { lat: number; lng: number } }) {
  const [latitude, setLatitude] = useState("");
  const [longitude, setLongitude] = useState("");
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    void api<{ metadata?: { geo?: { lat: number; lng: number } } }>("/match-svc/api/v1/organizations/me")
      .then((result) => {
        const geo = result.metadata?.geo;
        if (geo) {
          setLatitude(String(geo.lat));
          setLongitude(String(geo.lng));
          setEditing(false);
          setMessage("");
        } else {
          setEditing(true);
          setMessage("Set your facility location so it can participate in nearby matching.");
        }
      })
      .catch((error) => {
        setMessage(error instanceof Error ? error.message : "Facility profile could not be loaded.");
      });
  }, [api]);

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    try {
      await api("/match-svc/api/v1/organizations/me", {
        method: "PATCH",
        body: JSON.stringify({ facility_location: { lat: Number(latitude), lng: Number(longitude) } }),
      });
      setEditing(false);
      setMessage("Facility location saved. Refresh the request workspace to use it.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Facility location could not be saved.");
    } finally {
      setBusy(false);
    }
  };

  const coordinateText = latitude && longitude
    ? `${Number(latitude).toFixed(4)}, ${Number(longitude).toFixed(4)}`
    : "Not configured";

  return (
    <section className="feature-panel account-location-panel">
      <div className="feature-head">
        <div>
          <p className="eyebrow">Location</p>
          <h2>Facility location</h2>
        </div>
        <CardInfo
          title="Facility location"
          description="Stores the precise coordinates for the hospital or blood bank so nearby matching and routing can use the correct service point."
        />
      </div>

      {editing ? (
        <form onSubmit={save} className="agent-form account-location-form">
          <p>Enter the location of the hospital or blood bank itself.</p>
          <label>
            Facility latitude
            <input type="number" min="-90" max="90" step="any" value={latitude} onChange={(event) => setLatitude(event.target.value)} required />
          </label>
          <label>
            Facility longitude
            <input type="number" min="-180" max="180" step="any" value={longitude} onChange={(event) => setLongitude(event.target.value)} required />
          </label>
          {savedLocation && (
            <button
              type="button"
              className="secondary compact"
              onClick={() => {
                setLatitude(String(savedLocation.lat));
                setLongitude(String(savedLocation.lng));
              }}
            >
              Use my saved location
            </button>
          )}
          <button type="submit" className="primary compact" disabled={busy}>
            {busy ? "Saving…" : "Confirm facility location"}
          </button>
        </form>
      ) : (
        <div className="saved-region">
          <div className="saved-profile-header">
            <span className="badge neutral">Saved</span>
            <button type="button" className="secondary compact" onClick={() => setEditing(true)}>
              Edit location
            </button>
          </div>
          <div className="saved-region-value">
            <strong>Facility coordinates</strong>
            <span>{coordinateText}</span>
          </div>
        </div>
      )}

      {message && <p className="feature-note" role="status">{message}</p>}
    </section>
  );
}
