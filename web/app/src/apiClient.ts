import { getAuthToken } from "./authSession";

const API_BASE = (import.meta.env.VITE_BLOODNET_API_BASE_URL || "").replace(
  /\/$/,
  "",
);

export const identityPlatformEnabled = Boolean(
  import.meta.env.VITE_IDENTITY_PLATFORM_API_KEY &&
    import.meta.env.VITE_IDENTITY_PLATFORM_PROJECT_ID,
);

const productionIdentityAuth = import.meta.env.PROD && identityPlatformEnabled;
const API_KEY =
  import.meta.env.VITE_BLOODNET_GATEWAY_API_KEY ||
  import.meta.env.VITE_IDENTITY_PLATFORM_API_KEY ||
  "";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Convert transport failures into messages that are safe and useful in the UI. */
export function userFacingError(error: unknown, fallback = "We couldn't complete that request. Please try again."): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) {
    const message = String(error.message || "");
    if (message.toLowerCase().includes("failed to fetch") || message.toLowerCase().includes("networkerror")) {
      return "The browser could not reach the BloodNet gateway. Check the production Identity Platform / Firebase token, API Gateway consumer key, and Google Service Control / managed gateway service wiring before retrying.";
    }
    return "We couldn't reach BloodNet. Check your connection and try again.";
  }
  return fallback;
}

function formatApiErrorDetail(detail: unknown): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") {
          const message = item.msg || item.message || item.error;
          const location = Array.isArray(item.loc) ? item.loc.join(".") : "";
          return [message, location].filter(Boolean).join(" at ");
        }
        return String(item);
      })
      .filter(Boolean)
      .join("; ");
  }
  if (detail && typeof detail === "object") {
    const message = (detail as Record<string, unknown>).detail;
    return formatApiErrorDetail(message);
  }
  return "";
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const resolveToken = async (forceRefresh = false) =>
    productionIdentityAuth
      ? (await import("./identity")).getCurrentIdentityToken(forceRefresh).then(
          (token) => token || getAuthToken(),
        )
      : getAuthToken();
  let token = await resolveToken();
  const isFormData = options?.body instanceof FormData;
  const hasBody = options?.body !== undefined && options?.body !== null;
  const separator = path.includes("?") ? "&" : "?";
  const gatewayPath =
    productionIdentityAuth && API_KEY
      ? `${path}${separator}key=${encodeURIComponent(API_KEY)}`
      : path;
  let lastError: unknown;
  const canRetry = ["GET", "HEAD", "OPTIONS"].includes((options?.method || "GET").toUpperCase());
  for (let attempt = 0; attempt < 2; attempt += 1) {
    try {
      const response = await fetch(`${API_BASE}${gatewayPath}`, {
        credentials: "include",
        cache: "no-store",
        headers: {
          ...(hasBody && !isFormData ? { "Content-Type": "application/json" } : {}),
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        ...options,
      });
      const body = await response.json().catch(() => ({}));
      if (!response.ok) {
        // Firebase ID tokens are short lived. Refresh once before treating an
        // unauthorized response as a real signed-out session.
        if (response.status === 401 && productionIdentityAuth && attempt === 0) {
          token = await resolveToken(true);
          continue;
        }
        const message =
          response.status === 401
            ? "Your session has expired. Please sign in again."
            : response.status === 429
              ? "Too many requests. Please wait a moment and try again."
              : (() => {
                  const detailMessage = formatApiErrorDetail(body.detail);
                  if (detailMessage) return detailMessage;
                  if (response.status >= 500) return "BloodNet is temporarily unavailable. Please try again shortly.";
                  return `Request failed (${response.status})`;
                })();
        const error = new ApiError(message, response.status);
        if (!canRetry || response.status < 500 || attempt === 1) throw error;
        lastError = error;
        continue;
      }
      return body as T;
    } catch (error) {
      lastError = error;
      if (!canRetry || attempt === 1 || (error instanceof ApiError && error.status && error.status < 500)) throw error;
    }
  }
  if (lastError instanceof ApiError) throw lastError;
  if (lastError instanceof TypeError) {
    throw new ApiError("We couldn't reach BloodNet. Check your connection and try again.");
  }
  throw new ApiError("BloodNet service is temporarily unavailable.");
}
