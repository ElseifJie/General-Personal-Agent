import { useCallback, useEffect, useRef, useState } from "react";

import { rememberActiveConversation } from "./lastActive";
import {
  emitConversationsChanged,
  gpaApi,
  isAuthError,
  type Channel,
  type ConversationSummary,
  type TimelineItem,
  type TurnView,
} from "../gpaApi";

interface LoadedConversation {
  conversation: ConversationSummary | null;
  turns: TurnView[];
  items: TimelineItem[];
  runningTurnId: string | null;
}

interface ConversationState extends LoadedConversation {
  loading: boolean;
  error: string | null;
  connected: boolean;
  streaming: Record<string, string>;
}

const emptyState: ConversationState = {
  conversation: null,
  turns: [],
  items: [],
  loading: true,
  error: null,
  connected: false,
  runningTurnId: null,
  streaming: {},
};

async function loadTimeline(conversationId: string): Promise<TimelineItem[]> {
  const items: TimelineItem[] = [];
  let cursor = 0;
  for (let page = 0; page < 5; page += 1) {
    const response = await gpaApi.getTimeline(conversationId, cursor, 200);
    items.push(...response.items);
    if (!response.hasMore || response.nextCursor === null) break;
    cursor = response.nextCursor;
  }
  return items;
}

/**
 * Record the surface the user is actually on. Pending loads from a page that
 * has since been replaced (text → voice) must not overwrite the channel the
 * new page just recorded.
 */
function rememberIfOnSurface(
  conversationId: string,
  channel?: Channel,
): void {
  if (
    channel &&
    typeof window !== "undefined" &&
    window.location.pathname !== `/interact/${channel}/${conversationId}`
  ) {
    return;
  }
  rememberActiveConversation(conversationId, channel);
}

function mergeEvent(items: TimelineItem[], event: TimelineItem): TimelineItem[] {
  if (event.eventId && items.some((item) => item.eventId === event.eventId)) {
    return items;
  }
  return [...items, event].sort((a, b) => a.seq - b.seq);
}

/** Highest timeline sequence already loaded, used as the SSE replay cursor. */
function highestSeq(items: TimelineItem[]): number {
  let max = 0;
  for (const item of items) {
    if (Number.isFinite(item.seq) && item.seq > max) max = item.seq;
  }
  return max;
}

function eventToItem(data: Record<string, unknown>): TimelineItem {
  return {
    kind: "event",
    seq: Number(data.seq ?? 0),
    occurredAt: String(data.occurred_at ?? new Date().toISOString()),
    turnId: (data.turn_id as string | null) ?? null,
    taskId: (data.task_id as string | null) ?? null,
    eventId: (data.event_id as string | null) ?? null,
    type: (data.type as string | null) ?? null,
    payload: (data.payload as Record<string, unknown> | null) ?? null,
  };
}

export interface UseConversationResult {
  conversation: ConversationSummary | null;
  turns: TurnView[];
  items: TimelineItem[];
  loading: boolean;
  error: string | null;
  connected: boolean;
  runningTurnId: string | null;
  streaming: Record<string, string>;
  sending: boolean;
  refresh: () => Promise<void>;
  send: (content: string, attachmentIds?: string[]) => Promise<string | null>;
  stop: () => Promise<void>;
}

export function useConversation(
  conversationId: string | null | undefined,
  onAuthRequired: () => void,
  channel?: Channel,
): UseConversationResult {
  const [state, setState] = useState<ConversationState>(emptyState);
  const [sending, setSending] = useState(false);
  const authRef = useRef(onAuthRequired);
  authRef.current = onAuthRequired;

  /** Newest refresh request; stale responses must not touch state. */
  const refreshGenerationRef = useRef(0);
  /** Newest send request for this component instance. */
  const sendGenerationRef = useRef(0);
  /** Conversation the component currently shows, readable from async work. */
  const conversationIdRef = useRef<string | null>(conversationId ?? null);
  conversationIdRef.current = conversationId ?? null;
  /** One debounced refresh timer, merged instead of one per SSE event. */
  const refreshTimerRef = useRef<number | null>(null);
  /** Timeline cursor captured at subscription time (not a resubscribe trigger). */
  const lastSeqRef = useRef(0);
  lastSeqRef.current = highestSeq(state.items);

  const load = useCallback(async (): Promise<LoadedConversation | null> => {
    if (!conversationId) return null;
    const [detail, items] = await Promise.all([
      gpaApi.getConversation(conversationId),
      loadTimeline(conversationId),
    ]);
    if (
      !detail.conversation.isDraft &&
      detail.conversation.status !== "archived"
    ) {
      rememberIfOnSurface(conversationId, channel);
    }
    const running = detail.turns.find((turn) => turn.status === "running");
    return {
      conversation: detail.conversation,
      turns: detail.turns,
      items,
      runningTurnId: running?.turnId ?? null,
    };
  }, [conversationId, channel]);

  const refresh = useCallback(async () => {
    const targetId = conversationId;
    if (!targetId) return;
    const generation = refreshGenerationRef.current + 1;
    refreshGenerationRef.current = generation;
    const isCurrent = () =>
      refreshGenerationRef.current === generation &&
      conversationIdRef.current === targetId;
    try {
      const loaded = await load();
      if (!loaded || !isCurrent()) return;
      setState((current) => ({
        ...current,
        ...loaded,
        loading: false,
        error: null,
      }));
    } catch (error) {
      if (!isCurrent()) return;
      if (isAuthError(error)) {
        authRef.current();
        return;
      }
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : "加载交互失败",
      }));
    }
  }, [conversationId, load]);

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimerRef.current !== null) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  /**
   * Coalesce refreshes: replayed/live SSE frames frequently settle several
   * times in a burst, so only the last scheduled refresh should run.
   */
  const scheduleRefresh = useCallback(
    (delay: number) => {
      clearRefreshTimer();
      refreshTimerRef.current = window.setTimeout(() => {
        refreshTimerRef.current = null;
        void refresh();
      }, delay);
    },
    [clearRefreshTimer, refresh],
  );

  // Supersede any refresh that was still in flight for the previous conversation.
  useEffect(() => {
    refreshGenerationRef.current += 1;
    sendGenerationRef.current += 1;
    setSending(false);
  }, [conversationId]);

  // Drop a pending debounced refresh on conversation change and on unmount.
  useEffect(() => clearRefreshTimer, [clearRefreshTimer, conversationId]);

  useEffect(() => {
    let active = true;
    if (!conversationId) {
      setState({ ...emptyState, loading: false });
      return () => {
        active = false;
      };
    }
    setState({ ...emptyState, loading: true });
    void load()
      .then((loaded) => {
        if (!active || !loaded) return;
        setState({
          ...loaded,
          loading: false,
          error: null,
          connected: false,
          streaming: {},
        });
      })
      .catch((error: unknown) => {
        if (!active) return;
        if (isAuthError(error)) {
          authRef.current();
          return;
        }
        setState((current) => ({
          ...current,
          loading: false,
          error: error instanceof Error ? error.message : "加载交互失败",
        }));
      });
    return () => {
      active = false;
    };
  }, [conversationId, load]);

  useEffect(() => {
    // Wait for the initial load of *this* conversation: on a route change the
    // previous conversation's loading=false state must not open a socket with
    // the previous conversation's cursor.
    if (
      !conversationId ||
      state.loading ||
      state.conversation?.conversationId !== conversationId
    ) {
      return;
    }
    // Resume after what we already loaded; the server also sends `id:` frames,
    // so the browser's own reconnect carries the cursor forward.
    const source = new EventSource(
      `/api/conversations/${encodeURIComponent(conversationId)}/events?after_seq=${lastSeqRef.current}`,
    );
    const onReady = () => setState((current) => ({ ...current, connected: true }));
    const parse = (event: Event): Record<string, unknown> | null => {
      const data = (event as MessageEvent).data;
      if (typeof data !== "string" || !data) return null;
      try {
        return JSON.parse(data) as Record<string, unknown>;
      } catch {
        return null;
      }
    };
    const onDelta = (event: MessageEvent<string>) => {
      const data = parse(event);
      if (!data) return;
      const turnId = String(data.turn_id ?? "");
      const text = String((data.payload as Record<string, unknown> | null)?.text ?? "");
      if (!turnId) return;
      setState((current) => ({
        ...current,
        connected: true,
        runningTurnId: current.runningTurnId ?? turnId,
        streaming: {
          ...current.streaming,
          [turnId]: (current.streaming[turnId] ?? "") + text,
        },
      }));
    };
    const onStarted = () => {
      scheduleRefresh(60);
    };
    const onPersisted = (event: MessageEvent<string>) => {
      const data = parse(event);
      if (!data) return;
      setState((current) => ({
        ...current,
        connected: true,
        items: mergeEvent(current.items, eventToItem(data)),
      }));
    };
    const onSettled = (event: MessageEvent<string>) => {
      const data = parse(event);
      if (!data) return;
      const turnId = String(data.turn_id ?? "");
      setState((current) => {
        const streaming = { ...current.streaming };
        delete streaming[turnId];
        return {
          ...current,
          streaming,
          runningTurnId: current.runningTurnId === turnId ? null : current.runningTurnId,
        };
      });
      emitConversationsChanged();
      scheduleRefresh(80);
    };
    const onAuthInvalid = () => {
      source.close();
      authRef.current();
    };
    source.addEventListener("ready", onReady as EventListener);
    source.addEventListener("turn.started", onStarted as EventListener);
    source.addEventListener("message.delta", onDelta as EventListener);
    source.addEventListener("tool.call", onPersisted as EventListener);
    source.addEventListener("tool.result", onPersisted as EventListener);
    source.addEventListener("task.created", onPersisted as EventListener);
    source.addEventListener("task.status", onPersisted as EventListener);
    source.addEventListener("artifact", onPersisted as EventListener);
    source.addEventListener("error", onPersisted as EventListener);
    source.addEventListener("turn.settled", onSettled as EventListener);
    source.addEventListener("auth_invalid", onAuthInvalid as EventListener);
    source.onopen = () => setState((current) => ({ ...current, connected: true }));
    source.onerror = () => setState((current) => ({ ...current, connected: false }));
    return () => {
      source.close();
      clearRefreshTimer();
    };
  }, [
    clearRefreshTimer,
    conversationId,
    scheduleRefresh,
    state.conversation?.conversationId,
    state.loading,
  ]);

  const send = useCallback(
    async (content: string, attachmentIds: string[] = []): Promise<string | null> => {
      const text = content.trim();
      const targetId = conversationId;
      if (!targetId || !text) return null;
      const generation = sendGenerationRef.current + 1;
      sendGenerationRef.current = generation;
      const isCurrent = () =>
        sendGenerationRef.current === generation &&
        conversationIdRef.current === targetId;
      setSending(true);
      setState((current) => ({ ...current, error: null }));
      try {
        const accepted = await gpaApi.submitMessage(
          targetId,
          text,
          `${targetId}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          undefined,
          attachmentIds,
        );
        if (!isCurrent()) return null;
        rememberIfOnSurface(targetId, channel);
        setState((current) => ({
          ...current,
          runningTurnId: accepted.turnId,
        }));
        scheduleRefresh(80);
        return accepted.turnId;
      } catch (error) {
        if (!isCurrent()) return null;
        if (isAuthError(error)) {
          authRef.current();
          return null;
        }
        setState((current) => ({
          ...current,
          error: error instanceof Error ? error.message : "发送失败",
        }));
        return null;
      } finally {
        if (isCurrent()) setSending(false);
      }
    },
    [channel, conversationId, scheduleRefresh],
  );

  const stop = useCallback(async () => {
    if (!conversationId) return;
    try {
      await gpaApi.stopTurn(conversationId);
      scheduleRefresh(80);
    } catch (error) {
      if (isAuthError(error)) {
        authRef.current();
        return;
      }
      setState((current) => ({
        ...current,
        error: error instanceof Error ? error.message : "停止失败",
      }));
    }
  }, [conversationId, scheduleRefresh]);

  return {
    conversation: state.conversation,
    turns: state.turns,
    items: state.items,
    loading: state.loading,
    error: state.error,
    connected: state.connected,
    runningTurnId: state.runningTurnId,
    streaming: state.streaming,
    sending,
    refresh,
    send,
    stop,
  };
}
