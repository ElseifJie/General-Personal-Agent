/** Auth/system API client. GPA conversation/task APIs live in gpaApi.ts. */

export type UserRole = "user" | "admin";

export interface CurrentUser {
  userId: string;
  role: UserRole;
}

interface ApiErrorBody {
  code?: unknown;
  message?: unknown;
  requestId?: unknown;
}

export class ApiClientError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly requestId?: string,
  ) {
    super(message);
    this.name = "ApiClientError";
  }

  get isAuthRequired() {
    return this.status === 401;
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || "";

async function readError(response: Response): Promise<ApiClientError> {
  let body: ApiErrorBody | undefined;
  try {
    body = (await response.json()) as ApiErrorBody;
  } catch {
    body = undefined;
  }
  return new ApiClientError(
    response.status,
    typeof body?.code === "string" ? body.code : "REQUEST_FAILED",
    typeof body?.message === "string" ? body.message : "请求失败",
    typeof body?.requestId === "string" ? body.requestId : undefined,
  );
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined) headers.set("content-type", "application/json");
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    const error = await readError(response);
    if (error.isAuthRequired) window.dispatchEvent(new Event("wb:unauthorized"));
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiClient = {
  getMe: async (): Promise<CurrentUser> => {
    const row = await request<{ user_id: string; role: UserRole }>("/api/auth/me");
    return { userId: row.user_id, role: row.role };
  },
  login: (userId: string, pin: string) =>
    request<{ user_id: string; role: UserRole; ov_user: string }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ user_id: userId, pin }),
    }),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),
  listDemoUsers: () =>
    request<{ app_user_id: string; display_name: string; role: UserRole }[]>(
      "/api/demo/users",
    ),
};
