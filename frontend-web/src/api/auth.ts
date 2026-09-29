import { apiFetch } from "./client";

export const ACCESS_TOKEN_KEY = "cs-jobmate-access-token";
export interface AuthUser { id: number; email: string; created_at: string | null; }
export interface AuthResponse { access_token: string; token_type: "bearer"; user: AuthUser; }

export function getAccessToken(): string | null {
  return window.localStorage.getItem(ACCESS_TOKEN_KEY);
}

export function getCurrentUser() {
  return apiFetch<AuthUser>("/api/auth/me");
}

export async function login(email: string, password: string) {
  const result = await apiFetch<AuthResponse>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ email, password }),
  });
  window.localStorage.setItem(ACCESS_TOKEN_KEY, result.access_token);
  return result;
}

export async function register(email: string, password: string) {
  const result = await apiFetch<AuthResponse>("/api/auth/register", {
    method: "POST", body: JSON.stringify({ email, password }),
  });
  window.localStorage.setItem(ACCESS_TOKEN_KEY, result.access_token);
  return result;
}

export function logout() {
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
}
