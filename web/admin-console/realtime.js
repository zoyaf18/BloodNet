class SseCaseRealtimeAdapter {
    constructor(baseUrl, accessToken = "") {
        this.baseUrl = baseUrl;
        this.accessToken = accessToken;
    }

    subscribeToCase(caseId, onCase, onError = () => {}) {
        const controller = new AbortController();
        const consume = async () => {
            try {
                const response = await fetch(`${this.baseUrl}/match-svc/api/v1/cases/${encodeURIComponent(caseId)}/stream`, {
                    headers: this.accessToken ? { Authorization: `Bearer ${this.accessToken}` } : {},
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
                    buffer = messages.pop();
                    messages.forEach((message) => {
                        const data = message.split("\n").find((line) => line.startsWith("data: "));
                        if (data) onCase(JSON.parse(data.slice(6)));
                    });
                }
            } catch (error) {
                if (!controller.signal.aborted) onError(error);
            }
        };
        void consume();
        return () => controller.abort();
    }
}

window.bloodNetRealtime = new SseCaseRealtimeAdapter(
    (window.BLOODNET_API_BASE_URL || "").replace(/\/$/, ""),
    window.BLOODNET_ACCESS_TOKEN || "",
);
