import { Search } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import {
  emitConversationsChanged,
  gpaApi,
  isAuthError,
  type ConversationSummary,
} from "../gpaApi";
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  Input,
  PageHeader,
  Select,
  Spinner,
  type BadgeTone,
} from "../ui/index";
import { useConversations } from "./useConversations";

type Scope = "all" | "text" | "voice" | "archived";

function scopeFromParams(params: URLSearchParams): Scope {
  if (params.get("scope") === "archived") return "archived";
  const channel = params.get("channel");
  if (channel === "text" || channel === "voice") return channel;
  return "all";
}

/** The reference list shows a full calendar date, not a relative time. */
function formatListDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function statusFor(
  item: ConversationSummary,
  archived: boolean,
): { label: string; tone: BadgeTone } {
  if (archived) return { label: "已归档", tone: "neutral" };
  if (item.runningTaskCount > 0) return { label: "进行中", tone: "info" };
  if (!item.lastTurnPreview) return { label: "待开始", tone: "neutral" };
  return { label: "已完成", tone: "success" };
}

function channelBadge(item: ConversationSummary): {
  label: string;
  tone: BadgeTone;
} {
  const hasVoice = item.channels.includes("voice");
  const hasText = item.channels.includes("text");
  if (hasVoice && hasText) return { label: "语音+文本", tone: "info" };
  if (hasVoice) return { label: "语音", tone: "info" };
  return { label: "文本", tone: "neutral" };
}

export function ConversationHistoryPage({
  onAuthRequired,
}: {
  onAuthRequired: () => void;
}) {
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
  const scope = scopeFromParams(searchParams);
  const [query, setQuery] = useState(() => searchParams.get("query") ?? "");
  const [debouncedQuery, setDebouncedQuery] = useState(
    () => (searchParams.get("query") ?? "").trim(),
  );
  const archived = scope === "archived";
  const channel = scope === "text" || scope === "voice" ? scope : undefined;
  const { items, loading, loadingMore, error, nextCursor, refresh, loadMore } =
    useConversations(onAuthRequired, {
      limit: 30,
      archived,
      ...(channel ? { channel } : {}),
      ...(debouncedQuery ? { query: debouncedQuery } : {}),
    });
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setDebouncedQuery(query.trim()),
      300,
    );
    return () => window.clearTimeout(timer);
  }, [query]);

  function changeScope(next: Scope) {
    setActionError(null);
    const params = new URLSearchParams(searchParams);
    params.delete("channel");
    params.delete("scope");
    if (next === "text" || next === "voice") params.set("channel", next);
    if (next === "archived") params.set("scope", "archived");
    if (query.trim()) params.set("query", query.trim());
    else params.delete("query");
    setSearchParams(params, { replace: true });
  }

  function openConversation(item: ConversationSummary) {
    // History is the text/management surface. Continuing this
    // conversation by voice is the explicit mode switch inside the text page.
    navigate(`/interact/text/${item.conversationId}`);
  }

  async function restore(item: ConversationSummary) {
    if (restoringId) return;
    setRestoringId(item.conversationId);
    setActionError(null);
    try {
      await gpaApi.updateConversation(item.conversationId, { archived: false });
      emitConversationsChanged();
      await refresh();
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setActionError(caught instanceof Error ? caught.message : "恢复会话失败");
    } finally {
      setRestoringId(null);
    }
  }

  const emptyTitle = debouncedQuery
    ? "没有符合条件的历史会话"
    : scope === "archived"
      ? "还没有已归档会话"
      : scope === "all"
        ? "还没有会话"
        : `还没有${scope === "text" ? "文本" : "语音"}会话`;
  const emptyDescription = debouncedQuery
    ? "调整搜索词或会话范围后再试。"
    : scope === "archived"
      ? "在会话的更多操作中选择“归档”，会话会出现在这里。"
      : "从“新建会话”开始第一段对话，之后会出现在这里。";

  return (
    <div className="page gpa-history-page">
      <PageHeader title="历史会话" />

      <div className="gpa-history-filters">
        <label className="gpa-history-filter">
          <span className="gpa-history-filter__label">搜索历史会话</span>
          <span className="gpa-history-search">
            <Search size={16} aria-hidden="true" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="会话名称或最近内容"
              aria-label="搜索历史会话"
            />
          </span>
        </label>
        <label className="gpa-history-filter gpa-history-filter--scope">
          <span className="gpa-history-filter__label">会话范围</span>
          <Select
            value={scope}
            aria-label="会话范围"
            onChange={(event) => changeScope(event.target.value as Scope)}
          >
            <option value="all">全部会话</option>
            <option value="text">文本</option>
            <option value="voice">语音</option>
            <option value="archived">已归档</option>
          </Select>
        </label>
      </div>

      {error ? <Alert tone="danger">{error}</Alert> : null}
      {actionError ? <Alert tone="danger">{actionError}</Alert> : null}

      {loading ? (
        <div className="gpa-loading">
          <Spinner label="加载会话" />
          正在加载会话…
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title={emptyTitle}
          description={emptyDescription}
          action={
            debouncedQuery ? (
              <Button variant="secondary" onClick={() => setQuery("")}>
                清空搜索
              </Button>
            ) : scope !== "archived" ? (
              <Button onClick={() => navigate("/interact/text")}>
                开始交互
              </Button>
            ) : null
          }
        />
      ) : (
        <>
          <div className="gpa-history-list" role="list">
            {items.map((item) => {
              const status = statusFor(item, archived);
              const modality = channelBadge(item);
              return (
                <article
                  key={item.conversationId}
                  role="listitem"
                  className="gpa-history-card"
                >
                  <button
                    type="button"
                    className="gpa-history-card__main"
                    onClick={() => openConversation(item)}
                  >
                    <span className="gpa-history-card__title">
                      {item.title || "新交互"}
                    </span>
                    <span className="gpa-history-card__meta">
                      <time dateTime={item.updatedAt}>
                        {formatListDate(item.updatedAt)}
                      </time>
                    </span>
                  </button>
                  <div className="gpa-history-card__side">
                    <Badge tone={modality.tone}>{modality.label}</Badge>
                    <Badge tone={status.tone}>{status.label}</Badge>
                    {archived ? (
                      <Button
                        variant="text"
                        size="compact"
                        loading={restoringId === item.conversationId}
                        onClick={() => void restore(item)}
                      >
                        恢复
                      </Button>
                    ) : null}
                  </div>
                </article>
              );
            })}
          </div>
          {nextCursor ? (
            <div className="gpa-history-more">
              <Button
                variant="secondary"
                onClick={() => void loadMore()}
                loading={loadingMore}
              >
                加载更多
              </Button>
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
