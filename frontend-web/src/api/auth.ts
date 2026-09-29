import { apiFetch } from "./client";

export interface AuthUser { id: number; email: string; created_at: string | null; }
export interface AuthResponse { access_token: string; token_type: "bearer"; user: AuthUser; }

export async function login(email: string, password: string) {
  const result = await apiFetch<AuthResponse>("/api/auth/login", {
    method: "POST", body: JSON.stringify({ email, password }),
  });
  window.localStorage.setItem("cs-jobmate-access-token", result.access_token);
  return result;
}

export async function register(email: string, password: string) {
  const result = await apiFetch<AuthResponse>("/api/auth/register", {
    method: "POST", body: JSON.stringify({ email, password }),
  });
  window.localStorage.setItem("cs-jobmate-access-token", result.access_token);
  return result;
}

export function logout() {
  window.localStorage.removeItem("cs-jobmate-access-token");
}
