import { FormEvent, ReactNode, useEffect, useState } from "react";
import type { CaseView, ForecastPoint, InventoryUnit, InventoryReservation, Opportunity, AuditEvent, Recommendation, Identity, Role } from "./App";
import { api } from "./App";
import { RecommendationDetailDialog, RecommendationResult } from "./RecommendationUI";
import type { DecisionResult } from "./RecommendationUI";
import { CardInfo } from "./CardInfo";
import { NetworkIntelligencePanel, InventoryIntakeForm } from "./FeaturePanels";
import { summarizeForecast } from "./forecastPresentation";
import { usePagination } from "./Pagination";

type ExpiryRisk = { group: string; component: string; available_units: number; days_to_expiry: number; forecast_demand: number; stock_depth: number; risk: string };

function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge ${tone}`}>{typeof children === "string" ? children.replaceAll("_", " ") : children}</span>;
}

function Empty({ text }: { text: string }) {
  return <div className="empty"><span>○</span><p>{text}</p></div>;
}

function Stat({ label, value, note, description = note, alert = false }: { label: string; value: string | number; note: string; description?: string; alert?: boolean }) {
  return <div className={`stat card-with-info ${alert ? "alert" : ""}`}><CardInfo title={label} description={description} /><span>{label}</span><strong>{value}</strong><small>{note}</small></div>;
}

// ============================================================================
// Legacy intake presentation retained as a reusable, role-neutral component.
// ============================================================================
export function RequestIntakeViewEnhanced({ 
  rawText, setRawText, hospitalId, setHospitalId, submitRequest, selected, loading 
}: { 
  rawText: string; 
  setRawText: (value: string) => void; 
  hospitalId: string; 
  setHospitalId: (value: string) => void; 
  submitRequest: (event: FormEvent) => void; 
  selected: CaseView | null; 
  loading: boolean;
}) {
  const [history, setHistory] = useState<CaseView[]>([]);
    const [expiryRisks, setExpiryRisks] = useState<ExpiryRisk[]>([]);
  
  useEffect(() => {
    if (selected) {
      setHistory(prev => {
        const exists = prev.find(c => c.case.case_id === selected.case.case_id);
        return exists ? prev : [...prev, selected].slice(-5);
      });
    }
  }, [selected?.case.case_id]);

  const isUrgent = selected?.request?.urgency === "critical";
  const allUnitsSourced = selected && selected.case.units_from_donors_remaining === 0;
  
  return (
    <div className="request-intake-layout">
      <section className="request-intake-intro">
        <span className="number">01</span>
        <p className="eyebrow">Private request intake</p>
        <h2>Tell us what is happening.</h2>
        <p>BloodNet will structure your request, check nearby supply, and coordinate donor help. Internal donor details stay hidden.</p>
        <form onSubmit={submitRequest}>
          <label>
            Emergency details
            <textarea 
              value={rawText} 
              onChange={(event) => setRawText(event.target.value)} 
              placeholder="E.g., Need 3 O+ RBC units urgently for post-op patient"
              required 
            />
          </label>
          <label>
            Hospital ID
            <input 
              value={hospitalId} 
              onChange={(event) => setHospitalId(event.target.value)} 
              placeholder="Your hospital identifier"
              required 
            />
          </label>
          <button className="primary" disabled={loading}>
            {loading ? "Coordinating..." : "Start my request →"}
          </button>
        </form>
      </section>

      <section className="request-intake-status">
        <p className="eyebrow">02 / Fulfillment</p>
        <h2>{selected ? `Request ${selected.case.request_id}` : "No active request"}</h2>
        
        {selected ? (
          <>
            <div style={{ marginBottom: "16px" }}>
              <Badge tone={isUrgent ? "danger" : "success"}>{selected.case.reservation_state}</Badge>
              {isUrgent && <Badge tone="danger">Urgent</Badge>}
            </div>
            
            <div className="steps">
              <span className="done">✓ Request received</span>
              <span className="done">✓ Supply checked</span>
              <span className={allUnitsSourced ? "done" : "current"}>
                {allUnitsSourced ? "✓" : "○"} Donors being contacted
              </span>
              <span className={allUnitsSourced ? "done" : ""}>
                {allUnitsSourced ? "✓" : "○"} Fulfilled
              </span>
            </div>

            <div className="request-intake-numbers">
              <div>
                <strong>{selected.case.units_from_inventory}</strong>
                <span>units from inventory</span>
              </div>
              <div>
                <strong>{selected.case.units_from_donors_remaining}</strong>
                <span>units being found from donors</span>
              </div>
              <div>
                <strong>{Math.round(selected.case.fulfillment_probability * 100)}%</strong>
                <span>fulfillment confidence</span>
              </div>
            </div>

            {selected.swarm?.cohort_size && (
              <div style={{ marginTop: "12px", padding: "8px", backgroundColor: "rgba(79, 205, 196, 0.1)", borderRadius: "4px" }}>
                <small>
                  <strong>{selected.swarm.cohort_size}</strong> donors being contacted
                  {selected.swarm.donors_contacted?.length ? ` · ${selected.swarm.donors_contacted.length} responded` : ""}
                </small>
              </div>
            )}
          </>
        ) : (
          <Empty text="Submit a request to see safe fulfillment progress." />
        )}
      </section>

      {history.length > 1 && (
        <section className="request-intake-history">
          <p className="eyebrow">03 / Previous requests</p>
          <div style={{ fontSize: "12px", color: "#666" }}>
            {history.map((item) => (
              <div key={item.case.case_id} style={{ padding: "8px 0", borderBottom: "1px solid #eee" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span><strong>{item.case.request_id}</strong></span>
                  <Badge tone="success">completed</Badge>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

// ============================================================================
// HOSPITAL SURFACE - Enhanced with analytics and fulfillment tracking
// ============================================================================
export function HospitalSurfaceEnhanced({ identity }: { identity: Identity }) {
  const [fulfilled, setFulfilled] = useState<CaseView[]>([]);
  const [pending, setPending] = useState<CaseView[]>([]);
  const [driveNotifications, setDriveNotifications] = useState<{ notification_id: string; message: string; created_at: string }[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"all" | "urgent" | "pending">("all");

  useEffect(() => {
    setLoading(true);
    void api<{ cases?: CaseView[] }>(
      `/match-svc/api/v1/cases?hospital_id=${encodeURIComponent(identity.hospital_id || "")}`
    ).then((result) => {
      const allCases = Array.isArray(result.cases) ? result.cases : [];
      setFulfilled(allCases.filter((c) => c.case.outcome === "fulfilled"));
      setPending(allCases.filter((c) => !["fulfilled", "cancelled"].includes(c.case.outcome || "")));
    }).catch(() => {
      setFulfilled([]);
      setPending([]);
    }).finally(() => setLoading(false));
  }, [identity.hospital_id]);

  useEffect(() => {
    void api<{ notifications?: { notification_id: string; message: string; created_at: string }[] }>("/match-svc/api/v1/notifications")
      .then((result) => setDriveNotifications((result.notifications || []).filter((item) => item.message.toLowerCase().includes("donation drive"))))
      .catch(() => setDriveNotifications([]));
  }, [identity.organization?.id, identity.hospital_id]);

  const urgentPending = pending.filter(c => c.request?.urgency?.toLowerCase() === "critical");

  const filteredCases = (() => {
    switch (filter) {
      case "urgent":
        return urgentPending;
      case "pending":
        return pending;
      default:
        return [...fulfilled, ...pending];
    }
  })();

  return (
    <div className="hospital-surface">
      {driveNotifications.length > 0 && (
        <section className="notice bank-notification" aria-live="polite">
          <strong>Regional donation drive notice</strong>
          <span>{driveNotifications[0].message}</span>
          <small>{new Date(driveNotifications[0].created_at).toLocaleString()}</small>
        </section>
      )}
      <div className="stats-row">
        <Stat label="Fulfilled requests" value={fulfilled.length} note="Completed" />
        <Stat label="Open requests" value={pending.length} note="In progress" alert={urgentPending.length > 0} />
        <Stat label="Urgent cases" value={urgentPending.length} note="Need fast-track" alert={urgentPending.length > 0} />
      </div>

      <div className="filter-bar">
        <button 
          className={`filter-btn ${filter === "all" ? "active" : ""}`}
          onClick={() => setFilter("all")}
        >
          All ({fulfilled.length + pending.length})
        </button>
        <button 
          className={`filter-btn ${filter === "pending" ? "active" : ""}`}
          onClick={() => setFilter("pending")}
        >
          Pending ({pending.length})
        </button>
        <button 
          className={`filter-btn ${filter === "urgent" ? "active" : ""}`}
          onClick={() => setFilter("urgent")}
        >
          Urgent ({urgentPending.length})
        </button>
      </div>

      {loading ? (
        <Empty text="Loading cases..." />
      ) : filteredCases.length ? (
        <div className="case-table">
          {filteredCases.map((caseView) => (
            <div className="case-row" key={caseView.case.case_id}>
              <div className="case-info">
                <strong>{caseView.case.case_id}</strong>
                <small>{caseView.request?.group} {caseView.request?.component}</small>
              </div>
              <div className="case-metrics">
                <span>{caseView.case.units_from_inventory + (caseView.case.units_from_donors_fulfilled || 0)} units</span>
                <span>{Math.round(caseView.case.fulfillment_probability * 100)}%</span>
              </div>
              <Badge tone={
                caseView.case.outcome === "fulfilled" ? "success" :
                caseView.request?.urgency === "critical" ? "danger" :
                "warning"
              }>
                {caseView.case.reservation_state}
              </Badge>
            </div>
          ))}
        </div>
      ) : (
        <Empty text="No cases matching this filter." />
      )}
    </div>
  );
}

// ============================================================================
// BANK SURFACE - Enhanced with inventory deep-dive and expiry management
// ============================================================================
export function BankSurfaceEnhanced({ identity }: { identity: Identity }) {
  const [units, setUnits] = useState<InventoryUnit[]>([]);
  const [notifications, setNotifications] = useState<{ notification_id: string; message: string; created_at: string }[]>([]);
  const [queue, setQueue] = useState<Recommendation[]>([]);
  const [pendingReservationCount, setPendingReservationCount] = useState(0);
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<"inventory" | "reservations" | "queue" | "expiry">("inventory");
  const [reservations, setReservations] = useState<InventoryReservation[]>([]);
  const [expiryRisks, setExpiryRisks] = useState<ExpiryRisk[]>([]);
  const [filter, setFilter] = useState<string>("");
  const [decisionBusy, setDecisionBusy] = useState("");
  const [transferTarget, setTransferTarget] = useState("");
  const [transferUnits, setTransferUnits] = useState("");
  const [transferBusy, setTransferBusy] = useState(false);
  const [transferStatus, setTransferStatus] = useState("");
  const [copilotRecommendation, setCopilotRecommendation] = useState<Record<string, unknown> | null>(null);
  const [reservationId, setReservationId] = useState("");
  const [allocationBusy, setAllocationBusy] = useState(false);
  const [selectedRecommendation, setSelectedRecommendation] = useState<Recommendation | null>(null);
  const [decisionResult, setDecisionResult] = useState<DecisionResult | null>(null);
  const [decisionResults, setDecisionResults] = useState<Record<string, DecisionResult>>({});

  useEffect(() => {
    setLoading(true);
    void Promise.allSettled([
      api<{ units?: InventoryUnit[] }>("/match-svc/api/v1/inventory"),
      api<{ recommendations?: Recommendation[] }>("/match-svc/api/v1/reservations/pending"),
      api<{ recommendations?: Recommendation[] }>("/match-svc/api/v1/recommendations?state=AWAITING_APPROVAL")
      ,api<{ reservations?: InventoryReservation[] }>("/match-svc/api/v1/reservations")
      ,api<{ risks?: ExpiryRisk[] }>("/match-svc/api/v1/inventory/expiry-risk")
      ,api<{ notifications?: { notification_id: string; message: string; created_at: string }[] }>("/match-svc/api/v1/notifications")
    ]).then(([inventoryResult, pendingResult, genericResult, reservationResult, expiryResult, notificationResult]) => {
      const results = [inventoryResult, pendingResult, genericResult, reservationResult, expiryResult, notificationResult];
      const labels = ["inventory", "reservation approvals", "recommendations", "reservations", "expiry risks", "notifications"];
      const failed = results.flatMap((result, index) => result.status === "rejected" ? [labels[index]] : []);
      setLoadError(failed.length ? `Could not load ${failed.join(", ")}. Refresh to try again.` : "");
      const inventory = inventoryResult.status === "fulfilled" ? inventoryResult.value : {};
      const pending = pendingResult.status === "fulfilled" ? pendingResult.value : {};
      const generic = genericResult.status === "fulfilled" ? genericResult.value : {};
      const reservationLedger = reservationResult.status === "fulfilled" ? reservationResult.value : {};
      const expiry = expiryResult.status === "fulfilled" ? expiryResult.value : {};
      const notificationData = notificationResult.status === "fulfilled" ? notificationResult.value : {};
      setUnits(Array.isArray(inventory.units) ? inventory.units : []);
      setReservations(Array.isArray(reservationLedger.reservations) ? reservationLedger.reservations : []);
      setExpiryRisks(Array.isArray(expiry.risks) ? expiry.risks : []);
      setNotifications(Array.isArray(notificationData.notifications) ? notificationData.notifications : []);
      const reservationRecommendations = Array.isArray(pending.recommendations) ? pending.recommendations : [];
      const genericRecommendations = Array.isArray(generic.recommendations) ? generic.recommendations : [];
      setPendingReservationCount(reservationRecommendations.length);
      const bankRecommendations = genericRecommendations.filter((item) => item.type === "TRANSFER_INVENTORY");
      setQueue([...reservationRecommendations, ...bankRecommendations.filter((item) => !reservationRecommendations.some((existing) => existing.rec_id === item.rec_id))]);
    }).finally(() => setLoading(false));
  }, [identity.bank_id, refreshVersion]);

  useEffect(() => { setCopilotRecommendation(null); }, [transferTarget, transferUnits, filter]);

  const expiringUnits = units.filter(u => {
    const expiresAt = new Date(u.expires_at).getTime();
    const now = Date.now();
    return u.status === "available" && expiresAt >= now && expiresAt - now < 7 * 24 * 60 * 60 * 1000;
  });

  const availableUnits = units.filter(u => u.status === "available" && new Date(u.expires_at).getTime() > Date.now());
  const groupedUnits = availableUnits.reduce((acc, unit) => {
    const key = `${unit.group}|${unit.component}`;
    acc[key] = (acc[key] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  const filteredUnits = filter ? units.filter(u => u.group === filter) : units;
  const [visibleUnits, , unitsPagination] = usePagination(filteredUnits, 12);
  const [visibleReservations, , reservationsPagination] = usePagination(reservations, 8);
  const [visibleQueue, , queuePagination] = usePagination(queue, 6);
  const [visibleExpiryRisks, , expiryPagination] = usePagination(expiryRisks, 8);

  const decide = async (recommendation: Recommendation, decision: "approve" | "reject") => {
    setDecisionBusy(recommendation.rec_id);
    setActionError("");
    try {
      const path = recommendation.type === "INVENTORY_RESERVATION" && recommendation.case_id
        ? `/match-svc/api/v1/cases/${recommendation.case_id}/reservations/${recommendation.rec_id}/${decision}`
        : `/match-svc/api/v1/recommendations/${recommendation.rec_id}/${decision}`;
      const result = await api<DecisionResult>(path, {
        method: "POST",
        body: JSON.stringify({ rationale: `Reviewed by ${identity.subject_id}` }),
      });
      setQueue(current => current.filter(item => item.rec_id !== recommendation.rec_id));
      setDecisionResult(result);
      setDecisionResults(current => ({ ...current, [recommendation.rec_id]: result }));
      setRefreshVersion(version => version + 1);
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Could not save the approval decision.");
    } finally {
      setDecisionBusy("");
    }
  };

  const startTransfer = async () => {
    const recommendationPayload = copilotRecommendation?.payload as Record<string, unknown> | undefined;
    const recommendedUnitIds = Array.isArray(recommendationPayload?.unit_ids)
      ? recommendationPayload.unit_ids as string[]
      : [];
    if (!identity.bank_id || !transferTarget.trim() || !recommendedUnitIds.length) {
      setTransferStatus("Review a safe Copilot recommendation before dispatching a transfer.");
      return;
    }
    setTransferBusy(true); setTransferStatus("");
    try {
      await api("/match-svc/api/v1/inventory/transfers", { method: "POST", body: JSON.stringify({ from_bank: identity.bank_id, to_bank: transferTarget.trim(), unit_ids: recommendedUnitIds, reason: "Approved network balancing transfer" }) });
      setUnits(current => current.map(unit => recommendedUnitIds.includes(unit.unit_id) ? { ...unit, status: "in_transit" } : unit));
      setTransferStatus(`${recommendedUnitIds.length} unit(s) marked in transit.`); setTransferUnits("");
      setCopilotRecommendation(null); setRefreshVersion(version => version + 1);
    } catch (error) { setTransferStatus(error instanceof Error ? error.message : "Transfer failed"); }
    finally { setTransferBusy(false); }
  };

  const findSafeTransfer = async () => {
    const sample = availableUnits.find(unit => !filter || unit.group === filter);
    if (!sample || !transferTarget.trim()) { setTransferStatus("Choose available inventory and a destination bank."); return; }
    if (!Number.isInteger(Number(transferUnits)) || Number(transferUnits) < 1) { setTransferStatus("Quantity must be a positive whole number."); return; }
    setCopilotRecommendation(null);
    setTransferBusy(true); setTransferStatus("");
    try {
      const result = await api<{ status: string; data?: Record<string, unknown> }>("/copilot-svc/api/v1/copilot/transfer", { method: "POST", body: JSON.stringify({ destination_bank_id: transferTarget.trim(), group: sample.group, component: sample.component, quantity: Number(transferUnits) || 1 }) });
      setCopilotRecommendation(result.data || null);
      setTransferStatus(result.status === "success" ? "Safe transfer recommendation ready for review." : "No safe transfer recommendation is available.");
    } catch (error) { setTransferStatus(error instanceof Error ? error.message : "Copilot unavailable"); }
    finally { setTransferBusy(false); }
  };

  const consumeReservation = async () => {
    if (!reservationId.trim()) return;
    setAllocationBusy(true); setTransferStatus("");
    try {
      const id = reservationId.trim();
      await api(`/match-svc/api/v1/inventory/reservations/${encodeURIComponent(id)}/consume`, { method: "POST" });
      const consumedUnitIds = reservations.find(item => item.reservation_id === id)?.unit_ids || [];
      setReservations(current => current.map(item => item.reservation_id === id ? { ...item, status: "consumed", consumed_at: new Date().toISOString() } : item));
      setUnits(current => current.map(unit => consumedUnitIds.includes(unit.unit_id) ? { ...unit, status: "issued" } : unit));
      setRefreshVersion(version => version + 1);
      setTransferStatus(`${id} allocated and issued.`); setReservationId("");
    }
    catch (error) { setTransferStatus(error instanceof Error ? error.message : "Allocation failed"); }
    finally { setAllocationBusy(false); }
  };

  return (
    <div className="bank-surface">
      <button className="secondary compact" disabled={loading} onClick={() => setRefreshVersion(version => version + 1)}>Refresh bank data</button>
      {loadError && <div role="alert" className="notice">{loadError}</div>}
      {actionError && <div role="alert" className="notice">{actionError}</div>}
      {notifications.length > 0 && (
        <section className="notice bank-notification" aria-live="polite">
          <strong>New blood requirement</strong>
          <span>{notifications[0].message}</span>
          <small>{new Date(notifications[0].created_at).toLocaleString()}</small>
        </section>
      )}
      <div className="stats-row">
        <Stat label="Total units" value={units.length} note="In ledger" />
        <Stat label="Available" value={availableUnits.length} note="Ready to issue" />
        <Stat label="Expiring soon" value={expiringUnits.length} note="< 7 days" alert={expiringUnits.length > 0} />
        <Stat label="Reservations pending" value={pendingReservationCount} note="Inventory requests" alert={pendingReservationCount > 0} />
      </div>

      <div className="tabs">
        <button className={`tab ${view === "inventory" ? "active" : ""}`} onClick={() => setView("inventory")}>
          Inventory ({units.length})
        </button>
        <button className={`tab ${view === "reservations" ? "active" : ""}`} onClick={() => setView("reservations")}>
          Reservations ({reservations.filter((item) => item.status === "reserved").length})
        </button>
        <button className={`tab ${view === "queue" ? "active" : ""}`} onClick={() => setView("queue")}>
          Approval Queue ({queue.length})
        </button>
      </div>

      {view === "inventory" && identity.bank_id && <InventoryIntakeForm api={api} bankId={identity.bank_id} onSaved={() => setRefreshVersion((value) => value + 1)} />}
      {loading ? (
        <Empty text="Loading inventory..." />
      ) : view === "inventory" ? (
        <div className="inventory-view">
          <div className="bank-actions">
            <div><p className="eyebrow">Controlled allocation</p><strong>Issue reserved units</strong><input value={reservationId} onChange={(event) => setReservationId(event.target.value)} placeholder="Reservation ID" /></div>
            <button className="primary compact" onClick={() => void consumeReservation()} disabled={allocationBusy || !reservationId.trim()}>{allocationBusy ? "Issuing..." : "Issue units"}</button>
          </div>
          <div className="bank-actions">
            <div><p className="eyebrow">Beta · network transfer</p><strong>Move available units</strong><input value={transferTarget} onChange={(event) => setTransferTarget(event.target.value)} placeholder="Destination bank" /><input type="number" min="1" value={transferUnits} onChange={(event) => setTransferUnits(event.target.value)} placeholder="Quantity" /></div>
            <div className="actions"><button className="secondary compact" onClick={() => void findSafeTransfer()} disabled={transferBusy || !transferTarget.trim()}>{transferBusy ? "Checking..." : "Find safe transfer"}</button><button className="primary compact" onClick={() => void startTransfer()} disabled={transferBusy || !transferTarget.trim() || !copilotRecommendation}>{transferBusy ? "Transferring..." : "Dispatch recommended transfer"}</button></div>
          </div>
          {transferStatus && <div className="notice">{transferStatus}</div>}
          {copilotRecommendation && <div className="recommendation-result"><strong>Copilot recommendation</strong><p>{String(copilotRecommendation.rationale || "Review the proposed transfer before dispatch.")}</p><small>{JSON.stringify(copilotRecommendation.payload || {})}</small></div>}
          <div className="blood-group-summary">
            <p className="eyebrow">Available by blood group</p>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))", gap: "8px" }}>
              {Object.entries(groupedUnits).map(([product, count]) => {
                const [group, component] = product.split("|");
                return (
                <button
                  key={product}
                  className={`group-btn ${filter === group ? "active" : ""}`}
                  onClick={() => setFilter(filter === group ? "" : group)}
                >
                  <strong>{group} {component}</strong>
                  <span>{count} units</span>
                </button>
              )})}
            </div>
          </div>

          <div className="units-list">
            {filteredUnits.length ? (
              visibleUnits.map((unit) => {
                const daysToExpiry = Math.ceil((new Date(unit.expires_at).getTime() - Date.now()) / (24 * 60 * 60 * 1000));
                return (
                  <div className="unit-card" key={unit.unit_id}>
                    <div style={{ flex: 1 }}>
                      <strong>{unit.unit_id}</strong>
                      <small>{unit.group} {unit.component}</small>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <Badge tone={unit.status === "available" ? "success" : "warning"}>{unit.status}</Badge>
                      {daysToExpiry < 7 && (
                        <small style={{ display: "block", color: daysToExpiry < 2 ? "#d32f2f" : "#ff9800", marginTop: "4px" }}>
                          Expires in {daysToExpiry} days
                        </small>
                      )}
                    </div>
                  </div>
                );
              })
            ) : (
              <Empty text="No units match this filter." />
            )}
          </div>
          {unitsPagination}
        </div>
      ) : view === "reservations" ? (
        <div className="reservation-view">
          <div className="section-heading"><div><p className="eyebrow">Controlled inventory</p><h3>Reservation ledger</h3></div><span className="muted">{reservations.length} records</span></div>
          {reservations.length ? visibleReservations.map((reservation) => {
            const expiresAt = reservation.expires_at ? new Date(reservation.expires_at) : null;
            const expired = expiresAt ? expiresAt.getTime() <= Date.now() : false;
            const canIssue = reservation.status === "reserved" && !expired;
            return <div className="reservation-card" key={reservation.reservation_id}>
              <div><strong>{reservation.reservation_id}</strong><small>{reservation.case_id} · {reservation.unit_ids.length} unit(s)</small><small>Created {new Date(reservation.created_at).toLocaleString()}</small></div>
              <div className="reservation-card-actions"><Badge tone={canIssue ? "warning" : reservation.status === "consumed" ? "success" : "danger"}>{expired && reservation.status === "reserved" ? "expired" : reservation.status}</Badge>{expiresAt && <small>Expires {expiresAt.toLocaleDateString()}</small>}{canIssue && <button className="primary compact" onClick={() => { setReservationId(reservation.reservation_id); setView("inventory"); }}>Issue units</button>}</div>
            </div>;
          }) : <Empty text="No reservations are recorded for this bank." />}
          {reservationsPagination}
        </div>
      ) : view === "expiry" ? (
        <div className="units-list">
          {expiryRisks.length ? visibleExpiryRisks.map((risk) => <div className="unit-card" key={`${risk.group}-${risk.component}`}><div><strong>{risk.group} {risk.component}</strong><small>{risk.available_units} available · forecast demand {risk.forecast_demand}</small><small>Stock depth {risk.stock_depth}x · {risk.days_to_expiry} days to earliest expiry</small></div><Badge tone={risk.risk === "high" ? "danger" : risk.risk === "medium" ? "warning" : "success"}>{risk.risk} risk</Badge></div>) : <Empty text="No inventory risk data is available." />}
          {expiryPagination}
        </div>
      ) : (
        <div className="queue-view">
          {queue.length ? (
            visibleQueue.map((rec) => (
              <div className="queue-item" key={rec.rec_id}>
                <div>
                  <strong>{rec.rec_id}</strong>
                  <small>{rec.type.replaceAll("_", " ")}</small>
                  <p>{rec.rationale || "Review requested"}</p>
                </div>
                  <div style={{ textAlign: "right" }}>
                  <Badge tone="warning">{(rec.payload.unit_ids as string[] | undefined)?.length || rec.payload.target_units || 0} units</Badge>
                  <div style={{ marginTop: "8px", display: "flex", gap: "4px" }}>
                    <button className="text-button" onClick={() => { setSelectedRecommendation(rec); setDecisionResult(decisionResults[rec.rec_id] || null); }}>View detail</button>
                    <button className="primary compact" style={{ fontSize: "11px" }} disabled={decisionBusy === rec.rec_id} onClick={() => { setSelectedRecommendation(rec); setDecisionResult(null); }}>Review and approve</button>
                    <button className="secondary compact" style={{ fontSize: "11px" }} disabled={decisionBusy === rec.rec_id} onClick={() => void decide(rec, "reject")}>Reject</button>
                  </div>
                  <RecommendationResult result={decisionResults[rec.rec_id] || null} />
                </div>
              </div>
            ))
          ) : (
            <Empty text="No pending approval recommendations." />
          )}
          {queuePagination}
          <RecommendationDetailDialog recommendation={selectedRecommendation} result={decisionResult} open={selectedRecommendation !== null} onClose={() => { setSelectedRecommendation(null); setDecisionResult(null); }} onApprove={async () => { if (!selectedRecommendation) return; await decide(selectedRecommendation, "approve"); }} onReject={() => { if (selectedRecommendation) void decide(selectedRecommendation, "reject"); setSelectedRecommendation(null); }} />
        </div>
      )}
    </div>
  );
}

// ============================================================================
// DONOR SURFACE - Enhanced with history, stats, and preferences
// ============================================================================
export function DonorSurfaceEnhanced({ identity }: { identity: Identity }) {
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [history, setHistory] = useState<{ outreach_id: string; status: string; createdAt: string }[]>([]);
  const [status, setStatus] = useState<"loading" | "ready" | "unavailable">("loading");
  const [view, setView] = useState<"opportunities" | "history">("opportunities");

  useEffect(() => {
    void api<{ opportunities?: Opportunity[] }>(
      `/swarm-svc/api/v1/opportunities?donor_id=${encodeURIComponent(identity.subject_id || "")}`
    ).then((result) => {
      setOpportunities(Array.isArray(result.opportunities) ? result.opportunities : []);
      setHistory((result.opportunities || []).filter(item => item.status === "accepted" || item.status === "declined").map(item => ({ outreach_id: item.outreach_id, status: item.status, createdAt: item.created_at })));
      setStatus("ready");
    }).catch(() => setStatus("unavailable"));
  }, [identity.subject_id]);

  const respond = async (opportunity: Opportunity, response: "accept" | "decline") => {
    try {
      const result = await api<{ status: string }>(
        `/swarm-svc/api/v1/outreach/${opportunity.outreach_id}/response`,
        {
          method: "POST",
          body: JSON.stringify({ donor_id: identity.subject_id, response })
        }
      );
      setOpportunities((current) =>
        current.map((item) =>
          item.outreach_id === opportunity.outreach_id
            ? { ...item, status: result.status }
            : item
        )
      );
      setHistory(prev => [...prev.filter(item => item.outreach_id !== opportunity.outreach_id), { outreach_id: opportunity.outreach_id, status: result.status, createdAt: opportunity.created_at }]);
    } catch {
      setStatus("unavailable");
    }
  };

  const acceptedCount = history.filter(h => h.status === "accepted").length;
  const declinedCount = history.filter(h => h.status === "declined").length;
  const [visibleOpportunities, , opportunitiesPagination] = usePagination(opportunities.slice(1), 8);
  const [visibleHistory, , historyPagination] = usePagination(history, 10);

  if (status === "loading") return <Empty text="Loading donation opportunities..." />;

  return (
    <div className="donor-surface">
      <div className="stats-row">
        <Stat label="Active opportunities" value={opportunities.filter(o => o.status === "pending").length} note="Awaiting response" />
        <Stat label="Donations completed" value={acceptedCount} note="Fulfilled" />
        <Stat label="Declined" value={declinedCount} note="Past requests" />
        <Stat label="Response rate" value={acceptedCount + declinedCount > 0 ? `${Math.round((acceptedCount / (acceptedCount + declinedCount)) * 100)}%` : "—"} note="Acceptance rate" />
      </div>

      <div className="tabs">
        <button className={`tab ${view === "opportunities" ? "active" : ""}`} onClick={() => setView("opportunities")}>
          Opportunities ({opportunities.length})
        </button>
        <button className={`tab ${view === "history" ? "active" : ""}`} onClick={() => setView("history")}>
          History ({history.length})
        </button>
      </div>

      {view === "opportunities" ? (
        <div className="opportunities-view">
          {opportunities.length ? (
            <>
              {opportunities.slice(0, 1).map((opp) => (
                <div className="featured-opportunity" key={opp.outreach_id}>
                  <div className="opp-icon">⚡</div>
                  <div className="opp-content">
                    <Badge tone="danger">Urgent Request</Badge>
                    <h3>Help needed now</h3>
                    <p>{opp.message}</p>
                  </div>
                  <div className="opp-actions">
                    <button className="primary" disabled={opp.status !== "pending"} onClick={() => void respond(opportunities[0], "accept")}>
                      I can help
                    </button>
                  </div>
                </div>
              ))}

              <div className="other-opportunities">
                {visibleOpportunities.map((opportunity) => (
                  <div className="opportunity-card" key={opportunity.outreach_id}>
                    <div>
                      <strong>{opportunity.message}</strong>
                      <small>{new Date(opportunity.created_at).toLocaleString()}</small>
                    </div>
                    {opportunity.status === "pending" ? (
                      <div style={{ display: "flex", gap: "4px" }}>
                        <button className="primary compact" onClick={() => void respond(opportunity, "accept")}>Accept</button>
                        <button className="secondary compact" onClick={() => void respond(opportunity, "decline")}>Decline</button>
                      </div>
                    ) : (
                      <Badge tone={opportunity.status === "accepted" ? "success" : "warning"}>
                        {opportunity.status}
                      </Badge>
                    )}
                  </div>
                ))}
                {opportunitiesPagination}
              </div>
            </>
          ) : (
            <Empty text="No active donation opportunities right now." />
          )}
        </div>
      ) : (
        <div className="history-view">
          {history.length ? (
            visibleHistory.map((item) => (
              <div className="history-item" key={item.outreach_id}>
                <span>{new Date(item.createdAt).toLocaleDateString()}</span>
                <Badge tone={item.status === "accepted" ? "success" : "warning"}>
                  {item.status}
                </Badge>
              </div>
            ))
          ) : (
            <Empty text="Your donation history will appear here." />
          )}
          {historyPagination}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// REGIONAL SURFACE - Enhanced with risk analysis and swarm coordination
// ============================================================================
export function RegionalSurfaceEnhanced({ identity }: { identity: Identity }) {
  const [forecast, setForecast] = useState<ForecastPoint[]>([]);
  const [forecastStatus, setForecastStatus] = useState<"loading" | "ready" | "empty" | "scope_required" | "unavailable">("loading");
  const [forecastError, setForecastError] = useState("");
  const [forecastFreshness, setForecastFreshness] = useState<{ latestTargetDate?: string; isStale?: boolean }>({});
  const [swarms, setSwarms] = useState<{ case_id: string; status: string; cohort_size?: number }[]>([]);
  const [view, setView] = useState<"weather" | "swarms">("weather");
  const [visibleSwarms, , swarmsPagination] = usePagination(swarms, 10);

  useEffect(() => {
    if (!identity.region_id) {
      setForecast([]);
      setSwarms([]);
      setForecastStatus("scope_required");
      setForecastError("");
      return;
    }
    setForecastStatus("loading");
    setForecastError("");
    void Promise.allSettled([
      api<{ forecast?: ForecastPoint[]; status?: string; latest_target_date?: string; is_stale?: boolean }>(
        `/match-svc/api/v1/regional/forecast?region=${encodeURIComponent(identity.region_id || "")}&horizon_days=7`
      ),
      api<{ swarms?: { case_id: string; status: string; cohort_size?: number }[] }>(
        "/match-svc/api/v1/regional/swarms"
      )
    ]).then(([forecastResult, swarmResult]) => {
      if (forecastResult.status === "fulfilled") {
        const points = Array.isArray(forecastResult.value.forecast) ? forecastResult.value.forecast : [];
        setForecast(points);
        setForecastFreshness({ latestTargetDate: forecastResult.value.latest_target_date, isStale: forecastResult.value.is_stale });
        setForecastStatus(points.length ? "ready" : "empty");
      } else {
        setForecast([]);
        setForecastFreshness({});
        setForecastStatus("unavailable");
        setForecastError(forecastResult.reason instanceof Error ? forecastResult.reason.message : "Forecast service is unavailable.");
      }
      setSwarms(swarmResult.status === "fulfilled" && Array.isArray(swarmResult.value.swarms) ? swarmResult.value.swarms : []);
    });
  }, [identity.region_id]);

  const forecastSummary = summarizeForecast(forecast);
  const groupedForecast = Object.values(forecast.reduce<Record<string, {
    blood_group: string;
    demand: number;
    supply: number;
    gap: number;
    risk: number;
    firstDate: string;
    lastDate: string;
  }>>((groups, point) => {
    const demand = Number(point.predicted_demand) || 0;
    const supply = Number(point.projected_supply) || 0;
    const risk = Math.max(0, Number(point.shortage_probability) || 0);
    const current = groups[point.blood_group] || {
      blood_group: point.blood_group,
      demand: 0,
      supply,
      gap: 0,
      risk: 0,
      firstDate: point.target_date,
      lastDate: point.target_date,
    };
    current.demand += demand;
    current.supply = Math.min(current.supply, supply);
    current.gap = Math.max(current.gap, Math.max(0, demand - supply));
    current.risk = Math.max(current.risk, risk);
    current.firstDate = current.firstDate < point.target_date ? current.firstDate : point.target_date;
    current.lastDate = current.lastDate > point.target_date ? current.lastDate : point.target_date;
    groups[point.blood_group] = current;
    return groups;
  }, {})).sort((left, right) => left.blood_group.localeCompare(right.blood_group));

  return (
    <div className="regional-surface">
      <div className="stats-row">
        <Stat label="Forecast points" value={forecast.length} note="7-day horizon" description="Number of blood-group and component forecasts available for the next seven days in this region." />
        <Stat label="Blood groups at risk" value={forecastSummary.atRiskBloodGroups} note="7-day horizon" description="Number of distinct blood groups with a non-zero projected shortage probability in the selected region." />
        <Stat label="Critical shortages" value={forecastSummary.criticalBloodProducts} note="Shortage ≥ 70%" description="Number of distinct blood-group and component combinations whose shortage probability reaches at least 70 percent." alert={forecastSummary.criticalBloodProducts > 0} />
        <Stat label="Active swarms" value={swarms.length} note="Coordination" description="Number of active donor-mobilization workflows coordinating responses for open cases." />
      </div>

      <NetworkIntelligencePanel api={api} region={identity.region_id || ""} canRecommend />

      <div className="risk-summary card-with-info">
        <CardInfo title="Peak shortage risk" description="The highest forecast shortage probability across monitored blood groups over the next seven days." />
        <strong>Peak shortage risk — {Math.round(forecastSummary.peakProbability * 100)}% probability;</strong>
        <span>{Math.round(forecastSummary.projectedSupplyGap)} units in the projected supply gap</span>
      </div>

      <div className="tabs">
        <button className={`tab ${view === "weather" ? "active" : ""}`} onClick={() => setView("weather")}>
          Blood Weather
        </button>
        <button className={`tab ${view === "swarms" ? "active" : ""}`} onClick={() => setView("swarms")}>
          Swarms ({swarms.length})
        </button>
      </div>

      {view === "weather" ? (
        <div className="weather-view">
          {forecastFreshness.isStale && forecastFreshness.latestTargetDate && (
            <div className="notice" role="status">
              Showing the most recent published forecast, through {new Date(`${forecastFreshness.latestTargetDate}T00:00:00`).toLocaleDateString()}.
            </div>
          )}
          {forecastStatus === "loading" ? (
            <Empty text="Loading forecast..." />
          ) : forecastStatus === "scope_required" ? (
            <div className="regional-empty-state scope-required"><span aria-hidden="true">⌖</span><div><strong>Choose your service region</strong><p>Blood Weather needs a regional scope before it can load forecast data. Set it in Account → Your service region, then return here.</p></div></div>
          ) : forecast.length ? (
            <div className="weather-grid">
              {groupedForecast.map((group) => {
                const risk = group.risk;
                const riskTone = risk >= 0.7 ? "critical" : risk >= 0.4 ? "elevated" : risk > 0 ? "watch" : "healthy";
                return (
                  <article className={`weather-card risk-${riskTone}`} key={group.blood_group}>
                    <div className="weather-header">
                      <div><strong>{group.blood_group}</strong><span className="weather-risk-label">{riskTone}</span></div>
                      <small>7-day outlook</small>
                    </div>
                    <div className="weather-risk-row">
                      <div className="weather-risk-meter" aria-label={`${Math.round(risk * 100)}% shortage risk`}>
                        <i style={{ width: `${Math.round(risk * 100)}%` }} />
                      </div>
                      <strong>{Math.round(risk * 100)}%</strong>
                    </div>
                    <dl className="weather-metrics">
                      <div><dt>7-day demand</dt><dd>{group.demand.toFixed(1)}</dd></div>
                      <div><dt>Min supply</dt><dd>{group.supply.toFixed(1)}</dd></div>
                      <div className={group.gap > 0 ? "has-gap" : ""}><dt>Peak gap</dt><dd>{group.gap.toFixed(1)}</dd></div>
                    </dl>
                  </article>
                );
              })}
            </div>
          ) : (
            <div className="regional-empty-state"><span aria-hidden="true">◌</span><div><strong>{forecastStatus === "unavailable" ? "Forecast is temporarily unavailable" : "No forecast points for this region"}</strong><p>{forecastStatus === "unavailable" ? forecastError || "Please try again shortly." : "Forecast points will appear when supply and demand data has been published for this region."}</p></div></div>
          )}
        </div>
      ) : (
        <div className="swarms-view">
          {swarms.length ? (
            visibleSwarms.map((swarm) => (
              <div className="swarm-card" key={swarm.case_id}>
                <div>
                  <strong>{swarm.case_id}</strong>
                  {swarm.cohort_size && <small>{swarm.cohort_size} donors active</small>}
                </div>
                <Badge tone={swarm.status === "active" ? "danger" : swarm.status === "completed" ? "success" : "warning"}>
                  {swarm.status}
                </Badge>
              </div>
            ))
          ) : (
            <div className="regional-empty-state"><span aria-hidden="true">✓</span><div><strong>No active donor swarms</strong><p>This means there are no open cases currently requiring a donor-mobilization campaign in your region.</p></div></div>
          )}
          {swarmsPagination}
        </div>
      )}
    </div>
  );
}

// ============================================================================
// AUDIT SURFACE - Enhanced with filtering, export, and event details
// ============================================================================
export function AuditSurfaceEnhanced() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [filter, setFilter] = useState<"all" | string>("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    setLoading(true);
    setError("");
    void api<{ events: AuditEvent[] }>('/match-svc/api/v1/audit')
      .then((result) => setEvents(Array.isArray(result.events) ? result.events : []))
      .catch((reason: unknown) => {
        setEvents([]);
        setError(reason instanceof Error ? reason.message : "Audit events are unavailable.");
      })
      .finally(() => setLoading(false));
  }, []);

  const ordered = [...events].sort((left, right) => new Date(right.at).getTime() - new Date(left.at).getTime());
  const actions = [...new Set(events.map(e => e.action))];
  
  const filtered = filter === "all" 
    ? ordered 
    : ordered.filter(e => e.action === filter);

  const exportCsv = () => {
    const csv = [
      ["Timestamp", "Action", "Actor", "Case ID"],
      ...filtered.map(e => [
        new Date(e.at).toISOString(),
        e.action,
        e.actor || "system",
        e.case_id
      ])
    ].map(row => row.map(cell => `"${cell}"`).join(",")).join("\n");
    
    const blob = new Blob([csv], { type: "text/csv" });
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `audit-export-${new Date().toISOString().split("T")[0]}.csv`;
    a.click();
  };

  return (
    <div className="audit-surface">
      <div className="audit-header">
        <div>
          <p className="eyebrow">Recent activity</p>
          <h2>Audit log</h2>
          <small>{events.length ? `${events.length} recorded event${events.length === 1 ? "" : "s"}` : "No recorded events yet"}</small>
        </div>
        <button className="secondary compact" onClick={exportCsv} disabled={events.length === 0}>
          Export CSV
        </button>
      </div>

      {actions.length > 1 && <div className="filter-bar audit-filter-bar" aria-label="Filter audit events by type">
        <span>Event type</span>
        <div>
          <button 
            className={`filter-btn ${filter === "all" ? "active" : ""}`}
            onClick={() => setFilter("all")}
          >
            All activity ({events.length})
          </button>
          {actions.map(action => (
            <button
              key={action}
              className={`filter-btn ${filter === action ? "active" : ""}`}
              onClick={() => setFilter(action)}
            >
              {action} ({events.filter((event) => event.action === action).length})
            </button>
          ))}
        </div>
      </div>}

      {loading ? (
        <Empty text="Loading audit events..." />
      ) : filtered.length ? (
        <div className="event-list">
          {filtered.slice(0, 50).map((event, index) => (
            <div className="event-row" key={`${event.audit_id}-${event.at}-${index}`}>
              <div className="event-time">
                <span>{new Date(event.at).toLocaleTimeString()}</span>
                <small>{new Date(event.at).toLocaleDateString()}</small>
              </div>
              <div className="event-details">
                <strong>{event.action}</strong>
                <small>{event.actor || "system"} · {event.case_id}</small>
              </div>
              <div className="event-id">
                <code style={{ fontSize: "10px" }}>{event.audit_id.slice(0, 8)}…</code>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="audit-empty-state">
          <span aria-hidden="true">◌</span>
          <div>
            <strong>{error ? "Audit data is unavailable" : events.length ? "No events in this view" : "No audit events yet"}</strong>
            <p>{error || (events.length ? "Choose another event type to view its recorded activity." : "Recorded requests, approvals, and fulfillment actions will appear here. Use the search above when you have a known ID.")}</p>
          </div>
        </div>
      )}
    </div>
  );
}
