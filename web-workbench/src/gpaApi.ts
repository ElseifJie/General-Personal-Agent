/**
 * GPA-native conversation client (P2).
 *
 * The wire format is camelCase and authenticated by the existing `gpa_session`
 * cookie. This module deliberately stays separate from the legacy `api.ts`
 * client so the text workbench never depends on task-session semantics.
 */

export type Channel = "text" | "voice";
export type TurnStatus = "running" | "completed" | "failed" | "cancelled";

export interface ConversationSummary {
  conversationId: string;
  title: string;
  activeChannel: Channel;
  channels: Channel[];
  status: string;
  pinnedAt: string | null;
  createdAt: string;
  updatedAt: string;
  isDraft: boolean;
  taskCount: number;
  runningTaskCount: number;
  artifactCount: number;
  lastTurnPreview: string;
  lastTurnChannel: Channel | null;
}

export interface AttachmentView {
  attachmentId: string;
  fileId: string;
  name: string;
  mime: string | null;
  bytes: number | null;
  status: string;
  textStatus: string | null;
  downloadUrl: string | null;
}

export interface TurnView {
  turnId: string;
  conversationId: string;
  seq: number;
  channel: Channel;
  userText: string;
  assistantText: string;
  status: TurnStatus;
  traceId: string | null;
  metrics: Record<string, unknown>;
  attachments: AttachmentView[];
  createdAt: string;
  endedAt: string | null;
}

export interface ConversationDetail {
  conversation: ConversationSummary;
  turns: TurnView[];
}

export interface ConversationMemoryCommit {
  status: string;
  message: string;
}

export interface TimelineItem {
  kind: "turn" | "event";
  seq: number;
  occurredAt: string;
  turnId?: string | null;
  taskId?: string | null;
  channel?: Channel | null;
  userText?: string | null;
  assistantText?: string | null;
  status?: string | null;
  attachments?: AttachmentView[];
  eventId?: string | null;
  type?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface TimelinePage {
  items: TimelineItem[];
  nextCursor: number | null;
  hasMore: boolean;
}

export interface ArtifactRef {
  artifactId: string;
  taskId: string;
  name: string;
  mime: string | null;
  bytes: number | null;
  createdAt: string;
  downloadUrl: string | null;
}

export interface TaskView {
  taskId: string;
  conversationId: string;
  status: "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";
  progress: number;
  summary: string;
  executor: string;
  goal: string;
  taskType: string;
  artifacts: ArtifactRef[];
  error: Record<string, unknown> | null;
  createdAt: string;
  updatedAt: string;
}

export interface FileArtifact {
  artifactId: string;
  taskId: string;
  conversationId: string;
  conversationTitle: string;
  name: string;
  mime: string | null;
  bytes: number | null;
  createdAt: string;
  downloadUrl: string;
  taskGoal: string;
}

export interface ContextTree {
  rootUri: string;
  userUri: string;
  items: ContextNode[];
  truncated: boolean;
}

export interface ContextNode {
  uri: string;
  path: string;
  name: string;
  isDir: boolean;
  size: number | null;
  updatedAt: string | null;
}

export interface ContextContent {
  uri: string;
  content: string;
  truncated: boolean;
}

export interface ContextTraceView {
  traceId: string;
  turnId: string | null;
  conversationId: string;
  query: string;
  intent: string | null;
  sources: unknown;
  degraded: unknown;
  budget: Record<string, unknown>;
  channel: Channel | null;
  createdAt: string;
}

export interface FileObject {
  fileId: string;
  name: string;
  mime: string | null;
  bytes: number | null;
  status: string;
  textStatus: string | null;
  textChars: number;
  textTruncated: boolean;
  pageCount: number | null;
  error: string | null;
  createdAt: string;
  downloadUrl: string;
}

export interface ConversationFile {
  conversationFileId: string;
  fileId: string;
  name: string;
  mime: string | null;
  bytes: number | null;
  status: string;
  textStatus: string | null;
  textChars: number;
  textTruncated: boolean;
  pageCount: number | null;
  error: string | null;
  downloadUrl: string | null;
}

export interface KbEntry {
  uri: string;
  path: string;
  name: string;
  isDir: boolean;
  size: number | null;
  updatedAt: string | null;
  docId: string | null;
  status: string;
  scope: "private" | "shared";
  mime: string | null;
  downloadUrl: string | null;
  error: string | null;
  actions: string[];
}

export interface KbEntryPage {
  scope: string;
  rootUri: string;
  items: KbEntry[];
  degraded: string[];
}

export interface KbContent {
  uri: string;
  content: string;
  truncated: boolean;
}

export class GpaApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly traceId?: string,
  ) {
    super(message);
    this.name = "GpaApiError";
  }

  get isAuthRequired() {
    return this.status === 401;
  }
}

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) || "";

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && typeof init.body === "string") {
    headers.set("content-type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  if (!response.ok) {
    let body: Record<string, unknown> | undefined;
    try {
      body = (await response.json()) as Record<string, unknown>;
    } catch {
      body = undefined;
    }
    const error = new GpaApiError(
      response.status,
      typeof body?.code === "string" ? body.code : "REQUEST_FAILED",
      typeof body?.message === "string" ? body.message : "请求失败",
      typeof body?.trace_id === "string" ? body.trace_id : undefined,
    );
    if (error.isAuthRequired) window.dispatchEvent(new Event("wb:unauthorized"));
    throw error;
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function isAuthError(error: unknown): boolean {
  return error instanceof GpaApiError && error.isAuthRequired;
}

export function emitConversationsChanged(): void {
  window.dispatchEvent(new Event("gpa:conversations-changed"));
}

export const gpaApi = {
  listConversations: (
    params: {
      channel?: Channel;
      query?: string;
      archived?: boolean;
      hasRunningTask?: boolean;
      cursor?: string;
      limit?: number;
    } = {},
  ) =>
    request<{ items: ConversationSummary[]; nextCursor: string | null }>(
      `/api/conversations${query({
        channel: params.channel,
        query: params.query,
        archived: params.archived ? "true" : undefined,
        has_running_task: params.hasRunningTask ? "true" : undefined,
        cursor: params.cursor,
        limit: params.limit,
      })}`,
    ),

  updateConversation: (
    conversationId: string,
    body: { title?: string; pinned?: boolean; archived?: boolean },
  ) =>
    request<ConversationSummary>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
      { method: "PATCH", body: JSON.stringify(body) },
    ),

  deleteConversation: (conversationId: string) =>
    request<void>(`/api/conversations/${encodeURIComponent(conversationId)}`, {
      method: "DELETE",
    }),

  createConversation: (
    body: { title?: string; channel?: Channel; draft?: boolean } = {},
  ) =>
    request<{
      conversationId: string;
      activeChannel: Channel;
      channels: Channel[];
      title: string;
      createdAt: string;
    }>("/api/conversations", {
      method: "POST",
      body: JSON.stringify({
        title: body.title ?? "",
        channel: body.channel ?? "text",
        draft: body.draft ?? false,
      }),
    }),

  getConversation: (conversationId: string) =>
    request<ConversationDetail>(
      `/api/conversations/${encodeURIComponent(conversationId)}`,
    ),

  commitConversationMemory: (conversationId: string) =>
    request<ConversationMemoryCommit>(
      `/api/conversations/${encodeURIComponent(conversationId)}/memory/commit`,
      { method: "POST" },
    ),

  getTimeline: (conversationId: string, afterSeq = 0, limit = 200) =>
    request<TimelinePage>(
      `/api/conversations/${encodeURIComponent(conversationId)}/timeline${query({
        after_seq: afterSeq,
        limit,
      })}`,
    ),

  submitMessage: (
    conversationId: string,
    content: string,
    clientSubmissionId: string,
    channel: Channel = "text",
    attachmentIds: string[] = [],
  ) =>
    request<{ turnId: string; status: TurnStatus; eventsUrl: string }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ content, clientSubmissionId, channel, attachmentIds }),
      },
    ),

  stopTurn: (conversationId: string) =>
    request<{ turnId: string; status: "cancelling" | "cancelled" | "idle" }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/stop`,
      { method: "POST" },
    ),

  getTask: (taskId: string) =>
    request<TaskView>(`/api/tasks/${encodeURIComponent(taskId)}`),

  cancelTask: (taskId: string, reason = "") =>
    request<TaskView>(`/api/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  listFiles: (queryText = "", limit = 100) =>
    request<{ items: FileArtifact[] }>(
      `/api/files${query({ query: queryText, limit })}`,
    ),

  deleteFile: (artifactId: string) =>
    request<void>(`/api/files/${encodeURIComponent(artifactId)}`, {
      method: "DELETE",
    }),

  uploadFile: (
    file: File,
    purpose: "conversation" | "private_kb" | "shared_kb",
    options: { conversationId?: string; categoryId?: string } = {},
  ) => {
    const body = new FormData();
    body.set("file", file);
    body.set("purpose", purpose);
    if (options.conversationId) body.set("conversationId", options.conversationId);
    if (options.categoryId) body.set("categoryId", options.categoryId);
    return request<{
      fileId: string;
      conversationFileId?: string | null;
      docId?: string;
      name: string;
      mime: string | null;
      bytes: number | null;
      status: string;
      dedupe?: boolean;
    }>("/api/files", { method: "POST", body });
  },

  getFile: (fileId: string) =>
    request<FileObject>(`/api/files/${encodeURIComponent(fileId)}`),

  bindConversationFiles: (conversationId: string, fileIds: string[]) =>
    request<{ items: ConversationFile[] }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/files`,
      { method: "POST", body: JSON.stringify({ fileIds }) },
    ),

  listConversationFiles: (conversationId: string) =>
    request<{ items: ConversationFile[] }>(
      `/api/conversations/${encodeURIComponent(conversationId)}/files`,
    ),

  promoteFile: (fileId: string, scope: "private" | "shared", categoryId?: string) =>
    request<{ docId: string; dedupe: boolean }>(
      `/api/files/${encodeURIComponent(fileId)}/promote${query({ scope, categoryId })}`,
      { method: "POST" },
    ),

  reparseFile: (fileId: string) =>
    request<FileObject>(`/api/files/${encodeURIComponent(fileId)}/reparse`, { method: "POST" }),

  listKbEntries: (scope: "private" | "shared", path = "", search = "") =>
    request<KbEntryPage>(
      `/api/kb/entries${query({ scope, path, query: search })}`,
    ),

  getKbContent: (scope: "private" | "shared", uri: string) =>
    request<KbContent>(`/api/kb/content${query({ scope, uri })}`),

  deleteKbEntry: (scope: "private" | "shared", uri: string) =>
    request<{ docId: string; status: string }>(
      `/api/kb/entries${query({ scope, uri })}`,
      { method: "DELETE" },
    ),

  reindexKbDocument: (docId: string) =>
    request<KbEntry>(`/api/kb/documents/${encodeURIComponent(docId)}/reindex`, { method: "POST" }),

  retryDeleteKbDocument: (docId: string) =>
    request<KbEntry>(`/api/kb/documents/${encodeURIComponent(docId)}/retry-delete`, { method: "POST" }),

  listKbDeletions: () => request<{ items: KbEntry[] }>("/api/kb/deletions"),

  getContextTree: () => request<ContextTree>("/api/context/tree"),

  getContextContent: (uri: string) =>
    request<ContextContent>(`/api/context/content${query({ uri })}`),

  listContextTraces: (conversationId: string, limit = 20) =>
    request<ContextTraceView[]>(
      `/api/conversations/${encodeURIComponent(conversationId)}/context-traces${query({
        limit,
      })}`,
    ),
};

export function channelLabel(channel: Channel | null | undefined): string {
  return channel === "voice" ? "语音" : "文本";
}

export function taskStatusLabel(status: string | null | undefined): string {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "执行中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return labels[status ?? ""] ?? status ?? "未知";
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const now = new Date();
  const sameDay = date.toDateString() === now.toDateString();
  return sameDay
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : date.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      });
}
