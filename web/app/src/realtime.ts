import { getAuthToken } from "./authSession";
import { api } from "./apiClient";

export type CaseSnapshot = Record<string, unknown>;
export type CaseUpdate = (snapshot: CaseSnapshot) => void;
export type Unsubscribe = () => void;

export interface RealtimeAdapter {
  subscribeToCase(caseId: string, onUpdate: CaseUpdate, onError?: (error: Error) => void): Unsubscribe;
}

export class SseRealtimeAdapter implements RealtimeAdapter {
  constructor(private readonly baseUrl: string, private readonly accessToken = "") {}

  subscribeToCase(caseId: string, onUpdate: CaseUpdate, onError: (error: Error) => void = () => undefined): Unsubscribe {
    const controller = new AbortController();
    const consume = async () => {
      try {
        const accessToken = this.accessToken || getAuthToken();
        const response = await fetch(`${this.baseUrl}/match-svc/api/v1/cases/${encodeURIComponent(caseId)}/stream`, {
          headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error(`Realtime subscription failed (${response.status})`);
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (!controller.signal.aborted) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const messages = buffer.split("\n\n");
          buffer = messages.pop() || "";
          for (const message of messages) {
            const line = message.split("\n").find((item) => item.startsWith("data: "));
            if (line) onUpdate(JSON.parse(line.slice(6)) as CaseSnapshot);
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) onError(error instanceof Error ? error : new Error("Realtime subscription failed"));
      }
    };
    void consume();
    return () => controller.abort();
  }
}

export class PollingRealtimeAdapter implements RealtimeAdapter {
  subscribeToCase(caseId: string, onUpdate: CaseUpdate, onError: (error: Error) => void = () => undefined): Unsubscribe {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;
    const refresh = async () => {
      try {
        if (document.visibilityState !== "hidden") {
          const snapshot = await api<CaseSnapshot>(`/match-svc/api/v1/cases/${encodeURIComponent(caseId)}`, { signal: controller.signal });
          if (!controller.signal.aborted) onUpdate(snapshot);
          failures = 0;
        }
      } catch (error) {
        failures += 1;
        if (!controller.signal.aborted) onError(error instanceof Error ? error : new Error("Case updates are temporarily unavailable"));
      } finally {
        // Schedule after completion so slow requests never overlap. Stop all
        // work on navigation/logout and back off during an outage.
        if (!controller.signal.aborted) timer = setTimeout(refresh, Math.min(5000 * 2 ** failures, 30000));
      }
    };
    void refresh();
    return () => { controller.abort(); if (timer) clearTimeout(timer); };
  }
}

// Google API Gateway does not support streaming responses. Poll the durable,
// scoped case endpoint using the shared authentication/refresh API client.
export const realtimeAdapter: RealtimeAdapter = import.meta.env.PROD
  ? new PollingRealtimeAdapter()
  : new SseRealtimeAdapter((import.meta.env.VITE_BLOODNET_API_BASE_URL || "").replace(/\/$/, ""));
