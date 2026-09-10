const AUTH_STORAGE_KEY = "bloodnet_access_token";
let memoryToken = "";

export function getAuthToken(): string {
  if (typeof window === "undefined") return memoryToken;

  try {
    const sessionValue = window.sessionStorage.getItem(AUTH_STORAGE_KEY);
    if (sessionValue) {
      memoryToken = sessionValue;
      return sessionValue;
    }
  } catch {
    // Browsers may deny sessionStorage in restricted contexts; fall back to the in-memory token.
  }

  return memoryToken;
}

export function setAuthToken(token: string): void {
  memoryToken = token;

  if (typeof window === "undefined") return;

  try {
    window.sessionStorage.setItem(AUTH_STORAGE_KEY, token);
  } catch {
    // Ignore storage errors and keep the token in memory for the current page lifecycle.
  }
}

export function clearAuthToken(): void {
  memoryToken = "";

  if (typeof window === "undefined") return;

  try {
    window.sessionStorage.removeItem(AUTH_STORAGE_KEY);
  } catch {
    // Ignore storage errors during sign-out in restricted browser contexts.
  }
}

export function hasAuthToken(): boolean {
  return Boolean(getAuthToken());
}
