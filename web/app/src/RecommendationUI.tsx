import { ReactNode, useEffect, useState } from "react";
import { CardInfo } from "./CardInfo";
import { api as defaultApi } from "./apiClient";

export type Recommendation = {
  rec_id: string;
  type: string;
  state: string;
  case_id?: string;
  rationale?: string;
  expected_impact?: Record<string, number>;
  evidence?: Array<Record<string, unknown>>;
  options?: Array<Record<string, unknown>>;
  risks?: string[];
  assumptions?: string[];
  provenance?: Record<string, unknown>;
  payload: Record<string, unknown>;
};

export type ExecutionResult = {
  status?: string;
  execution_id?: string;
  remaining_shortfall?: number;
  shortfall_after_execution?: number;
  failure_reason?: string;
  rollback_status?: string;
  escalation_required?: boolean;
  escalation_next_step?: string;
  escalation_recommendation?: Recommendation;
  result?: { drive_status?: string; region?: string; recipients?: number; notifications?: string[] };
};

export type DecisionResult = {
  recommendation?: Recommendation;
  execution?: ExecutionResult | null;
  case?: { case?: { outcome?: string; escalation_state?: string; units_from_inventory?: number; units_from_donors_remaining?: number } };
};

function DetailRow({ label, value }: { label: string; value: ReactNode }) {
  return <div className="recommendation-detail-row"><strong>{label}</strong><div className="recommendation-detail-value">{value}</div></div>;
}

function humanLabel(value: string) {
  return value.replaceAll("_", " ").replace(/([a-z])([A-Z])/g, "$1 $2");
}

export function recommendationLabel(type: string) {
  return type === "CREATE_DONATION_DRIVE" ? "Regional donation drive" : humanLabel(type);
}

function HumanValue({ value }: { value: unknown }): ReactNode {
  if (value === null || value === undefined || value === "") return "Not provided";
  if (Array.isArray(value)) return value.length ? <ul className="metadata-list">{value.map((item, index) => <li key={index}><HumanValue value={item} /></li>)}</ul> : "None";
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>);
    return entries.length ? <dl className="metadata-list metadata-object">{entries.map(([key, item]) => <div key={key}><dt>{humanLabel(key)}</dt><dd><HumanValue value={item} /></dd></div>)}</dl> : "None";
  }
  return typeof value === "boolean" ? (value ? "Yes" : "No") : String(value);
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (value && typeof value === "object") return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${humanLabel(key)}: ${formatValue(item)}`).join("\n");
  return String(value ?? "-");
}

function RecommendationSopEvidence({ api, recommendation }: { api: <T>(path: string, options?: RequestInit) => Promise<T>; recommendation: Recommendation }) {
  const [passages, setPassages] = useState<Array<{ document_id: string; title: string; content: string; citation: string }>>([]);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    setUnavailable(false);
    void api<{ passages: Array<{ document_id: string; title: string; content: string; citation: string }> }>("/agent-svc/api/v1/sops/search", {
      method: "POST",
      body: JSON.stringify({ query: `${recommendation.type} ${recommendation.rationale || "blood allocation guidance"}` }),
    }).then((result) => setPassages(result.passages.slice(0, 2))).catch(() => { setPassages([]); setUnavailable(true); });
  }, [api, recommendation.rec_id]);
  return <div className="recommendation-sop-evidence"><div><strong>Applicable SOP evidence</strong><CardInfo title="Applicable SOP evidence" description="Read-only passages retrieved from the approved SOP corpus for this recommendation. Citations are required and remain supporting evidence; approval is still a human decision." /></div>{passages.map((passage) => <article key={passage.citation}><strong>{passage.title}</strong><p>{passage.content}</p><cite>{passage.citation}</cite></article>)}{unavailable && <small>SOP evidence is temporarily unavailable.</small>}</div>;
}

export function RecommendationResult({ result }: { result?: DecisionResult | null }) {
  const execution = result?.execution;
  const escalation = execution?.escalation_recommendation;
  if (!result || !execution) return null;
  const status = execution.status || result.recommendation?.state || "completed";
  const tone = status === "failed" ? "danger" : execution.escalation_required ? "warning" : "success";
  return <div className={`recommendation-result ${tone}`}>
    <div className="result-heading"><strong>{status.replaceAll("_", " ")}</strong>{execution.execution_id && <small>{execution.execution_id}</small>}</div>
    {execution.remaining_shortfall !== undefined && <DetailRow label="Remaining shortfall" value={`${execution.remaining_shortfall} unit(s)`} />}
    {execution.failure_reason && <DetailRow label="Failure reason" value={execution.failure_reason} />}
    {execution.rollback_status && <DetailRow label="Rollback" value={execution.rollback_status.replaceAll("_", " ")} />}
    {execution.result?.drive_status && <DetailRow label="Drive action" value={`${execution.result.drive_status.replaceAll("_", " ")} · ${execution.result.recipients || 0} notification recipient(s)`} />}
    {escalation && <div className="escalation-callout"><strong>Escalation recommended</strong><span>{escalation.type.replaceAll("_", " ")} · {formatValue(escalation.payload.target_units)} unit(s)</span><small>{String(escalation.payload.trigger || "Follow-up action required")}</small></div>}
  </div>;
}

export function RecommendationDetailDialog({ recommendation, result, open, onClose, onApprove, onReject, canApprove = true, api }: {
  recommendation: Recommendation | null;
  result?: DecisionResult | null;
  open: boolean;
  onClose: () => void;
  onApprove: () => Promise<void>;
  onReject?: () => void;
  canApprove?: boolean;
  api?: <T>(path: string, options?: RequestInit) => Promise<T>;
}) {
  const [actionBusy, setActionBusy] = useState(false);
  const [actionError, setActionError] = useState("");
  if (!recommendation) return null;
  const payloadEntries = Object.entries(recommendation.payload || {});
  const approve = async () => {
    setActionBusy(true);
    setActionError("");
    try {
      await onApprove();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : "Unable to approve this recommendation.");
    } finally {
      setActionBusy(false);
    }
  };
  return <div className={`recommendation-dialog-backdrop ${open ? "open" : ""}`} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className="recommendation-dialog" role="dialog" aria-modal="true" aria-labelledby="recommendation-dialog-title">
      <div className="feature-head"><div><p className="eyebrow">Recommendation detail</p><h2 id="recommendation-dialog-title">{recommendationLabel(recommendation.type)}</h2><small>{recommendation.rec_id}{recommendation.case_id ? ` · ${recommendation.case_id}` : ""}</small></div><button className="text-button" onClick={onClose} aria-label="Close recommendation detail">Close</button></div>
      <p className="feature-note">{recommendation.rationale || "Evidence-backed intervention proposal."}</p>
      <div className="recommendation-detail-grid">
        <DetailRow label="State" value={recommendation.state.replaceAll("_", " ")} />
        {recommendation.evidence?.length ? <DetailRow label="Evidence" value={recommendation.evidence.map((item) => formatValue(item)).join(" · ")} /> : null}
        {recommendation.options?.length ? <DetailRow label="Options" value={recommendation.options.map((item) => formatValue(item)).join(" · ")} /> : null}
        {recommendation.expected_impact && Object.keys(recommendation.expected_impact).length ? <DetailRow label="Expected impact" value={<HumanValue value={recommendation.expected_impact} />} /> : null}
        {recommendation.risks?.length ? <DetailRow label="Risks" value={recommendation.risks.join(" · ")} /> : null}
        {recommendation.assumptions?.length ? <DetailRow label="Assumptions" value={recommendation.assumptions.join(" · ")} /> : null}
        {recommendation.provenance && Object.keys(recommendation.provenance).length ? <DetailRow label="Provenance" value={<HumanValue value={recommendation.provenance} />} /> : null}
        {payloadEntries.map(([key, value]) => <DetailRow key={key} label={humanLabel(key)} value={<HumanValue value={value} />} />)}
      </div>
      <RecommendationSopEvidence api={api || defaultApi} recommendation={recommendation} />
      <RecommendationResult result={result} />
      {actionError && <div className="notice error recommendation-action-error" role="alert">{actionError}</div>}
      {open && !result?.execution && <div className="actions dialog-actions">{canApprove && <button className="primary" onClick={() => void approve()} disabled={actionBusy}>{actionBusy ? "Approving…" : "Approve recommendation"}</button>}{canApprove && onReject && <button className="secondary" onClick={onReject} disabled={actionBusy}>Reject</button>}</div>}
      {result?.execution && <div className="actions dialog-actions"><button className="secondary" onClick={onClose}>Done</button></div>}
    </section>
  </div>;
}
