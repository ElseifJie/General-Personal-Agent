import {
  Bot,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Download,
  ExternalLink,
  Eye,
  FileText,
  Loader2,
  MessageSquare,
  Wrench,
} from "lucide-react";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import { Markdown } from "../components/Markdown";
import {
  channelLabel,
  formatTime,
  type Channel,
  type TimelineItem,
} from "../gpaApi";
import { Alert, Badge, Spinner } from "../ui/index";

function statusTone(status: string | null | undefined) {
  if (status === "completed" || status === "ok") return "success" as const;
  if (status === "failed" || status === "error") return "danger" as const;
  if (status === "partial" || status === "cancelled") return "warning" as const;
  if (status === "running" || status === "queued") return "info" as const;
  return "neutral" as const;
}

function statusLabel(status: string | null | undefined) {
  const labels: Record<string, string> = {
    queued: "排队中",
    running: "执行中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    cancelled: "已取消",
    cancelling: "取消中",
  };
  return labels[status ?? ""] ?? status ?? "未知";
}

function payloadOf(item: TimelineItem): Record<string, unknown> {
  return item.payload ?? {};
}

function textOf(payload: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return "";
}

/** Status updates reuse the same taskId; the timeline must render one card. */
function taskEventId(item: TimelineItem): string {
  if (item.kind !== "event") return "";
  if (item.type !== "task.created" && item.type !== "task.status") return "";
  return item.taskId || textOf(payloadOf(item), "taskId", "task_id");
}

function TurnBlock({
  item,
  streaming,
  running,
}: {
  item: TimelineItem;
  streaming?: string;
  running: boolean;
}) {
  const channel = (item.channel ?? "text") as Channel;
  const assistant = streaming ?? item.assistantText ?? "";
  const failed = item.status === "failed";
  return (
    <article className="gpa-turn" data-seq={item.seq}>
      {item.userText ? (
        <div className="gpa-user">
          <div className="gpa-user__bubble">{item.userText}</div>
          <p className="gpa-user__meta">
            <span>你</span>
            <span aria-hidden="true">·</span>
            <span>{channelLabel(channel)}</span>
            <span aria-hidden="true">·</span>
            <time dateTime={item.occurredAt}>{formatTime(item.occurredAt)}</time>
          </p>
          {item.attachments && item.attachments.length > 0 ? (
            <ul className="gpa-turn-attachments" aria-label="本轮附件">
              {item.attachments.map((attachment) => (
                <li key={attachment.attachmentId || attachment.fileId}>
                  <FileText size={12} aria-hidden="true" />
                  {attachment.downloadUrl ? (
                    <a href={attachment.downloadUrl} download>
                      {attachment.name}
                    </a>
                  ) : (
                    <span>{attachment.name}</span>
                  )}
                  <span className="gpa-turn-attachments__status">
                    {attachment.textStatus === "ready"
                      ? "已解析"
                      : attachment.textStatus === "failed"
                        ? "解析失败"
                        : attachment.textStatus === "skipped"
                          ? "无文本"
                          : "解析中"}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
      <div className="gpa-assistant">
        <span className="gpa-avatar" aria-hidden="true">
          <Bot size={15} />
        </span>
        <div className="gpa-assistant__content">
          <div className="gpa-assistant__head">
            <span className="gpa-assistant__name">GPA</span>
            {running ? (
              <span className="gpa-live">
                <span className="gpa-live__dot" aria-hidden="true" />
                正在回复
              </span>
            ) : failed ? (
              <span className="gpa-live gpa-live--error">
                <CircleAlert size={12} aria-hidden="true" />
                本轮未完成
              </span>
            ) : (
              <time className="gpa-assistant__time" dateTime={item.occurredAt}>
                {formatTime(item.occurredAt)}
              </time>
            )}
          </div>
          {assistant ? (
            <div className="gpa-assistant__body">
              <Markdown>{assistant}</Markdown>
            </div>
          ) : running ? (
            <p className="gpa-assistant__pending">
              <Spinner label="等待回复" size={14} />
              正在思考…
            </p>
          ) : (
            <p className="gpa-assistant__pending">本轮没有生成文字回复。</p>
          )}
        </div>
      </div>
    </article>
  );
}

function TaskEvent({ item }: { item: TimelineItem }) {
  const payload = payloadOf(item);
  const taskId = item.taskId || textOf(payload, "taskId");
  const status = textOf(payload, "status") || item.status || "queued";
  const title =
    textOf(payload, "goal", "summary", "taskType") || `任务 ${taskId || ""}`;
  const progress =
    typeof payload.progress === "number" ? Math.round(payload.progress * 100) : null;
  return (
    <div className="gpa-event gpa-event--task">
      <span className="gpa-event__icon" aria-hidden="true">
        {status === "running" || status === "queued" ? (
          <Loader2 size={15} className="spin" />
        ) : status === "failed" || status === "partial" ? (
          <CircleAlert size={15} />
        ) : (
          <CircleCheck size={15} />
        )}
      </span>
      <div className="gpa-event__body">
        <div className="gpa-event__line">
          <strong>{title}</strong>
          <Badge tone={statusTone(status)}>{statusLabel(status)}</Badge>
          {item.type === "task.created" ? (
            <span className="gpa-event__kind">新任务</span>
          ) : null}
        </div>
        {progress !== null ? (
          <div className="gpa-progress" aria-label={`进度 ${progress}%`}>
            <span style={{ width: `${Math.max(0, Math.min(100, progress))}%` }} />
          </div>
        ) : null}
        {textOf(payload, "summary") ? (
          <p className="gpa-event__note">{textOf(payload, "summary")}</p>
        ) : null}
        <div className="gpa-event__actions">
          {taskId ? (
            <Link className="gpa-link" to={`/tasks/${encodeURIComponent(taskId)}`}>
              查看任务
              <ExternalLink size={12} aria-hidden="true" />
            </Link>
          ) : null}
          <time dateTime={item.occurredAt}>{formatTime(item.occurredAt)}</time>
        </div>
      </div>
    </div>
  );
}

function ArtifactEvent({
  item,
  onSelect,
}: {
  item: TimelineItem;
  onSelect?: (artifactId: string) => void;
}) {
  const payload = payloadOf(item);
  const artifactId = textOf(payload, "artifactId", "artifact_id");
  const taskId = item.taskId || textOf(payload, "taskId", "task_id");
  const name = textOf(payload, "name") || artifactId || "产物";
  const downloadUrl =
    artifactId && taskId
      ? `/api/tasks/${encodeURIComponent(taskId)}/artifacts/${encodeURIComponent(
          artifactId,
        )}/download`
      : null;
  return (
    <div className="gpa-event gpa-event--artifact">
      <span className="gpa-event__icon" aria-hidden="true">
        <FileText size={15} />
      </span>
      <div className="gpa-event__body">
        <div className="gpa-event__line">
          <strong>{name}</strong>
          <span className="gpa-event__kind">产物</span>
        </div>
        <div className="gpa-event__actions">
          {artifactId && onSelect ? (
            <button
              type="button"
              className="gpa-link gpa-link--button"
              onClick={() => onSelect(artifactId)}
            >
              <Eye size={12} aria-hidden="true" />
              查看
            </button>
          ) : null}
          {downloadUrl ? (
            <a className="gpa-link" href={downloadUrl}>
              <Download size={12} aria-hidden="true" />
              下载
            </a>
          ) : null}
          {taskId ? (
            <Link className="gpa-link" to={`/tasks/${encodeURIComponent(taskId)}`}>
              所属任务
            </Link>
          ) : null}
          <time dateTime={item.occurredAt}>{formatTime(item.occurredAt)}</time>
        </div>
      </div>
    </div>
  );
}

function ToolEvent({ item }: { item: TimelineItem }) {
  const payload = payloadOf(item);
  const name = textOf(payload, "name") || "工具";
  const isResult = item.type === "tool.result";
  return (
    <details className="gpa-tool">
      <summary>
        <Wrench size={12} aria-hidden="true" />
        <span>
          {isResult ? "工具返回" : "调用工具"} · {name}
        </span>
      </summary>
      <pre>{JSON.stringify(payload, null, 2)}</pre>
    </details>
  );
}

/** A run of tool events folds into one quiet row, like the reference session. */
function ToolGroup({ items }: { items: TimelineItem[] }) {
  const names = [
    ...new Set(
      items.map((item) => textOf(payloadOf(item), "name") || "工具"),
    ),
  ];
  return (
    <details className="gpa-tool-group">
      <summary>
        <ChevronRight
          size={13}
          aria-hidden="true"
          className="gpa-tool-group__chevron"
        />
        <span>执行过程 · {items.length} 步</span>
        <span className="gpa-tool-group__names">{names.join(" · ")}</span>
      </summary>
      <div className="gpa-tool-group__body">
        {items.map((item) => (
          <ToolEvent
            key={`${item.type}-${item.seq}-${item.eventId ?? ""}`}
            item={item}
          />
        ))}
      </div>
    </details>
  );
}

function GenericEvent({ item }: { item: TimelineItem }) {
  const payload = payloadOf(item);
  if (item.type === "error") {
    return (
      <Alert tone="danger">{textOf(payload, "message") || "本轮出现错误"}</Alert>
    );
  }
  return (
    <div className="gpa-event gpa-event--notice">
      <span className="gpa-event__icon" aria-hidden="true">
        <CircleAlert size={15} />
      </span>
      <div className="gpa-event__body">
        <div className="gpa-event__line">
          <strong>{item.type || "通知"}</strong>
          <time dateTime={item.occurredAt}>{formatTime(item.occurredAt)}</time>
        </div>
      </div>
    </div>
  );
}

function EventBlock({
  item,
  onArtifactSelect,
}: {
  item: TimelineItem;
  onArtifactSelect?: (artifactId: string) => void;
}) {
  if (item.type === "task.created" || item.type === "task.status") {
    return <TaskEvent item={item} />;
  }
  if (item.type === "artifact") {
    return <ArtifactEvent item={item} onSelect={onArtifactSelect} />;
  }
  if (item.type === "tool.call" || item.type === "tool.result") {
    return <ToolEvent item={item} />;
  }
  return <GenericEvent item={item} />;
}

export function ConversationTimeline({
  items,
  streaming,
  runningTurnId,
  emptyTitle = "还没有交互",
  emptyDescription = "在下方输入第一句话，开始这段对话。",
  onArtifactSelect,
}: {
  items: TimelineItem[];
  streaming: Record<string, string>;
  runningTurnId: string | null;
  emptyTitle?: string;
  emptyDescription?: string;
  onArtifactSelect?: (artifactId: string) => void;
}) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = endRef.current;
    if (!node) return;
    const viewport = node.closest(".timeline-shell");
    if (viewport instanceof HTMLElement) {
      viewport.scrollTop = viewport.scrollHeight;
      return;
    }
    const nearBottom =
      window.innerHeight + window.scrollY >= document.body.offsetHeight - 240;
    if (nearBottom) node.scrollIntoView({ block: "end" });
  }, [items, streaming]);

  if (items.length === 0) {
    return (
      <div className="gpa-transcript-empty">
        <span className="gpa-transcript-empty__icon" aria-hidden="true">
          <MessageSquare size={20} />
        </span>
        <p className="gpa-transcript-empty__title">{emptyTitle}</p>
        <p className="gpa-transcript-empty__description">{emptyDescription}</p>
      </div>
    );
  }

  const groups: Array<
    | { kind: "item"; key: string; item: TimelineItem }
    | { kind: "tools"; key: string; items: TimelineItem[] }
  > = [];
  let pendingTools: TimelineItem[] = [];
  const flushTools = () => {
    if (pendingTools.length === 0) return;
    groups.push({
      kind: "tools",
      key: `tools-${pendingTools[0].seq}`,
      items: pendingTools,
    });
    pendingTools = [];
  };
  const latestTask = new Map<string, TimelineItem>();
  for (const item of items) {
    const taskId = taskEventId(item);
    if (taskId) latestTask.set(taskId, item);
  }
  const renderedTask = new Set<string>();
  for (const item of items) {
    if (
      item.kind === "event" &&
      (item.type === "tool.call" || item.type === "tool.result")
    ) {
      pendingTools.push(item);
      continue;
    }
    flushTools();
    const taskId = taskEventId(item);
    if (taskId) {
      // First occurrence keeps the card in place; later status events only
      // refresh it, so one task never produces multiple cards.
      if (renderedTask.has(taskId)) continue;
      renderedTask.add(taskId);
    }
    groups.push({
      kind: "item",
      key:
        item.kind === "turn"
          ? `turn-${item.turnId || item.seq}`
          : `event-${item.eventId || item.seq}`,
      item: taskId ? latestTask.get(taskId) ?? item : item,
    });
  }
  flushTools();

  return (
    <div className="gpa-transcript" role="log" aria-live="polite">
      {groups.map((group) => {
        if (group.kind === "tools") {
          return <ToolGroup key={group.key} items={group.items} />;
        }
        const item = group.item;
        if (item.kind === "turn") {
          const turnId = item.turnId ?? "";
          return (
            <TurnBlock
              key={group.key}
              item={item}
              streaming={streaming[turnId]}
              running={runningTurnId === turnId || item.status === "running"}
            />
          );
        }
        return (
          <EventBlock
            key={group.key}
            item={item}
            onArtifactSelect={onArtifactSelect}
          />
        );
      })}
      <div ref={endRef} />
    </div>
  );
}
