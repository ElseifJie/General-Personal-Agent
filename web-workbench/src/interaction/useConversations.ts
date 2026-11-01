import { useCallback, useEffect, useRef, useState } from "react";

import {
  gpaApi,
  isAuthError,
  type Channel,
  type ConversationSummary,
} from "../gpaApi";

interface ConversationsState {
  items: ConversationSummary[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  nextCursor: string | null;
}

export function useConversations(
  onAuthRequired: () => void,
  options: {
    channel?: Channel;
    query?: string;
    archived?: boolean;
    limit?: number;
  } = {},
) {
  const { channel, query, archived = false, limit = 30 } = options;
  const [state, setState] = useState<ConversationsState>({
    items: [],
    loading: true,
    loadingMore: false,
    error: null,
    nextCursor: null,
  });
  const loadingMoreRef = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const response = await gpaApi.listConversations({
        channel,
        query,
        archived,
        limit,
      });
      setState({
        items: response.items,
        loading: false,
        loadingMore: false,
        error: null,
        nextCursor: response.nextCursor,
      });
    } catch (error) {
      if (isAuthError(error)) {
        onAuthRequired();
        return;
      }
      setState((current) => ({
        ...current,
        loading: false,
        loadingMore: false,
        error: error instanceof Error ? error.message : "加载会话失败",
      }));
    }
  }, [archived, channel, query, limit, onAuthRequired]);

  const loadMore = useCallback(async () => {
    const cursor = state.nextCursor;
    if (!cursor || loadingMoreRef.current) return;
    loadingMoreRef.current = true;
    setState((current) => ({ ...current, loadingMore: true, error: null }));
    try {
      const response = await gpaApi.listConversations({
        channel,
        query,
        archived,
        limit,
        cursor,
      });
      setState((current) => {
        const seen = new Set(
          current.items.map((item) => item.conversationId),
        );
        return {
          ...current,
          items: [
            ...current.items,
            ...response.items.filter(
              (item) => !seen.has(item.conversationId),
            ),
          ],
          loadingMore: false,
          error: null,
          nextCursor: response.nextCursor,
        };
      });
    } catch (error) {
      if (isAuthError(error)) {
        setState((current) => ({ ...current, loadingMore: false }));
        onAuthRequired();
        return;
      }
      setState((current) => ({
        ...current,
        loadingMore: false,
        error: error instanceof Error ? error.message : "加载更多会话失败",
      }));
    } finally {
      loadingMoreRef.current = false;
    }
  }, [archived, channel, query, limit, state.nextCursor, onAuthRequired]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const onChange = () => void refresh();
    const onFocus = () => void refresh();
    window.addEventListener("gpa:conversations-changed", onChange);
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("gpa:conversations-changed", onChange);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  return { ...state, refresh, loadMore };
}
