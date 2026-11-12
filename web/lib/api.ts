export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  signal?: AbortSignal;
}

/**
 * The single entry point for the backend.
 *
 * Every state-changing request carries `X-MoodleAI: 1`, which app/api.py requires.
 * Requests stay same-origin (Next rewrites /api to 127.0.0.1:8765), so the
 * backend's host/Origin/CSP model is never widened.
 */
export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET") headers["X-MoodleAI"] = "1";

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
      signal: options.signal,
    });
  } catch {
    throw new ApiError(
      "Cannot reach the local MoodleAI server. Is `python main.py` still running?",
      0,
    );
  }

  const type = response.headers.get("content-type") ?? "";
  const payload = type.includes("application/json")
    ? await response.json().catch(() => null)
    : await response.text().catch(() => "");

  if (!response.ok) {
    const detail =
      (payload && typeof payload === "object" && "detail" in payload && payload.detail) ||
      (payload && typeof payload === "object" && "error" in payload && payload.error) ||
      (typeof payload === "string" && payload) ||
      `${response.status} ${response.statusText}`;
    throw new ApiError(String(detail), response.status);
  }

  return payload as T;
}

export function buildDownloadUrl(node: { root?: string; path?: string }): string {
  const path = String(node.path ?? "")
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
  return `/api/files/download?root=${encodeURIComponent(node.root ?? "workspace")}&path=${path}`;
}
