import { applyActionCode, createUserWithEmailAndPassword, getAuth, sendEmailVerification, signInWithEmailAndPassword, signOut, updateProfile } from "firebase/auth";
import { initializeApp } from "firebase/app";

// The Firebase web API key is public to the browser; the real security boundary
// remains Identity Platform policies, backend authorization, and server-side token verification.
const firebaseConfig = {
  apiKey: import.meta.env.VITE_IDENTITY_PLATFORM_API_KEY,
  authDomain: import.meta.env.VITE_IDENTITY_PLATFORM_AUTH_DOMAIN,
  projectId: import.meta.env.VITE_IDENTITY_PLATFORM_PROJECT_ID,
};

export const identityPlatformEnabled = Boolean(firebaseConfig.apiKey && firebaseConfig.projectId);

const app = identityPlatformEnabled ? initializeApp(firebaseConfig) : null;
const auth = app ? getAuth(app) : null;

function ensureFirebaseAuth() {
  if (!identityPlatformEnabled || !auth) {
    throw new Error("Identity Platform is not configured for this environment.");
  }
  return auth;
}

export async function signIn(email: string, password: string): Promise<string> {
  const firebaseAuth = ensureFirebaseAuth();
  let result;
  try {
    result = await signInWithEmailAndPassword(firebaseAuth, email, password);
  } catch (error: unknown) {
    if (typeof error === "object" && error && "code" in error && error.code === "auth/invalid-credential") {
      throw new Error(
        "This BloodNet account is not yet active in the sign-in directory. If you already submitted a blood-bank access request, ask a regional administrator to verify it. Otherwise, check your password or create/migrate your Firebase account.",
      );
    }
    throw error;
  }
  if (!result.user.emailVerified) throw new Error("Verify your email before signing in.");
  return result.user.getIdToken(true);
}

/**
 * Return the Firebase SDK's current ID token for an already signed-in user.
 * The SDK refreshes expired ID tokens using its persisted refresh token.
 */
export async function getCurrentIdentityToken(forceRefresh = false): Promise<string | null> {
  if (!auth) return null;
  await auth.authStateReady();
  return auth.currentUser?.getIdToken(forceRefresh) ?? null;
}

export async function signUp(email: string, password: string, displayName: string): Promise<void> {
  const firebaseAuth = ensureFirebaseAuth();
  const result = await createUserWithEmailAndPassword(firebaseAuth, email, password);
  await updateProfile(result.user, { displayName });
  await sendEmailVerification(result.user, {
    url: `${window.location.origin}/?mode=verify`,
    handleCodeInApp: true,
  });
  await signOut(firebaseAuth);
}

export async function resendVerification(email: string, password: string): Promise<void> {
  const firebaseAuth = ensureFirebaseAuth();
  const result = await signInWithEmailAndPassword(firebaseAuth, email, password);
  if (result.user.emailVerified) {
    await signOut(firebaseAuth);
    throw new Error("This email is already verified. You can sign in.");
  }
  await sendEmailVerification(result.user, {
    url: `${window.location.origin}/?mode=verify`,
    handleCodeInApp: true,
  });
  await signOut(firebaseAuth);
}

export async function verifyEmailActionCode(code: string): Promise<void> {
  await applyActionCode(ensureFirebaseAuth(), code);
}

export async function signOutIdentity(): Promise<void> {
  if (!auth) return;
  await signOut(auth);
}

