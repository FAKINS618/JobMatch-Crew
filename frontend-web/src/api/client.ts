export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export function resolveApiUrl(path: string): string {
  const baseUrl = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
  return baseUrl ? `${baseUrl}${path}` : path;
}

function readErrorDetail(payload: unknown): string {
  if (typeof payload === "object" && payload !== null && "detail" in payload) {
    const detail = payload.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      return detail
        .filter((item): item is { msg?: string } => typeof item === "object" && item !== null)
        .map((item) => item.msg ?? "请求字段不符合要求")
        .join("；");
    }
  }
  return "请求失败，请稍后重试";
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = window.localStorage.getItem("cs-jobmate-access-token");
  const response = await fetch(resolveApiUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null);
    if (response.status === 401 && token && path !== "/api/auth/login" && path !== "/api/auth/register") {
      window.localStorage.removeItem("cs-jobmate-access-token");
      window.dispatchEvent(new Event("jobmatch-auth-expired"));
    }
    throw new ApiError(readErrorDetail(payload), response.status);
  }
  return (await response.json()) as T;
}
