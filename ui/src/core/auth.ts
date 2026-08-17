/**
 * Auth for the browser SPA.
 *
 * Decided in docs/UI_PLAN.md §8: the access token lives in memory, the refresh
 * token in localStorage. No API change, which keeps development simple.
 *
 * What that trades away, written at the call site so it isn't rediscovered by
 * surprise: **a refresh token in localStorage is readable by any XSS on the
 * page.** The blast radius is bounded — this API is read-only, so a stolen
 * token reads data but cannot alter, delete or spend anything — but it is a
 * real data-exposure risk, not a theoretical one.
 *
 * DEFERRED, NOT DROPPED: before any deployment reachable outside a trusted
 * network, move the refresh token to an httpOnly, SameSite=Strict, Secure
 * cookie. That is a small API change (set/clear the cookie on /auth/login and
 * /auth/refresh) plus CORS credentials — a couple of hours, not a redesign,
 * which is exactly why deferring it is safe rather than a gamble. This is the
 * exit criterion for going public, tracked in docs/UI_PLAN.md §8.
 */

const REFRESH_KEY = "dxb.refresh_token";

/**
 * Access token, in memory only — deliberately not persisted, so it dies with
 * the tab. Module scope rather than a store because the fetch client needs it
 * outside React's render cycle.
 */
let accessToken: string | null = null;

export function getAccessToken(): string | null {
  return accessToken;
}

export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getRefreshToken(): string | null {
  try {
    return localStorage.getItem(REFRESH_KEY);
  } catch {
    // Private-mode / disabled storage: degrade to session-only auth rather
    // than crashing the app on boot.
    return null;
  }
}

export function setRefreshToken(token: string | null): void {
  try {
    if (token === null) localStorage.removeItem(REFRESH_KEY);
    else localStorage.setItem(REFRESH_KEY, token);
  } catch {
    /* see getRefreshToken */
  }
}

export function clearTokens(): void {
  setAccessToken(null);
  setRefreshToken(null);
}

export function isAuthenticated(): boolean {
  return accessToken !== null;
}
