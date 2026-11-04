import {
  Archive,
  Mic,
  Pencil,
  Pin,
  PinOff,
  Plus,
  Trash2,
} from "lucide-react";
import { useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import {
  emitConversationsChanged,
  gpaApi,
  isAuthError,
  type ConversationSummary,
} from "../gpaApi";
import {
  Button,
  Dialog,
  Field,
  Input,
  RowActionsMenu,
  Spinner,
  type RowActionItem,
} from "../ui/index";

const VISIBLE_LIMIT = 8;

function relativeTime(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 60) return "刚刚";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分钟`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}小时`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}天`;
  if (days < 30) return `${Math.floor(days / 7)}周`;
  if (date.getFullYear() === new Date().getFullYear()) {
    return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  }
  return date.toLocaleDateString("zh-CN");
}

function titleOf(conversation: ConversationSummary): string {
  return conversation.title || "新会话";
}

export function ConversationRailList({
  conversations,
  loading,
  onAuthRequired,
}: {
  conversations: ConversationSummary[];
  loading: boolean;
  onAuthRequired: () => void;
}) {
  const location = useLocation();
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [renameTarget, setRenameTarget] = useState<ConversationSummary | null>(
    null,
  );
  const [renameDraft, setRenameDraft] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renamePending, setRenamePending] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ConversationSummary | null>(
    null,
  );
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [deletePending, setDeletePending] = useState(false);

  const visible = useMemo(
    () =>
      expanded ? conversations : conversations.slice(0, VISIBLE_LIMIT),
    [conversations, expanded],
  );
  const remaining = Math.max(0, conversations.length - visible.length);

  const activeId =
    location.pathname.match(/^\/interact\/(?:text|voice)\/([^/]+)$/)?.[1] ??
    null;
  const newHref = location.pathname.startsWith("/interact/voice")
    ? "/interact/voice?fresh=1"
    : "/interact/text";
  const hrefFor = (conversation: ConversationSummary) =>
    `/interact/text/${conversation.conversationId}`;

  function failure(caught: unknown): string | null {
    if (isAuthError(caught)) {
      onAuthRequired();
      return null;
    }
    return caught instanceof Error ? caught.message : "操作失败，请重试。";
  }

  function openRename(conversation: ConversationSummary) {
    setRenameTarget(conversation);
    setRenameDraft(titleOf(conversation));
    setRenameError(null);
  }

  async function saveRename() {
    if (!renameTarget || renamePending) return;
    const title = renameDraft.trim();
    if (!title) {
      setRenameError("会话名称不能为空。");
      return;
    }
    if (title === titleOf(renameTarget)) {
      setRenameTarget(null);
      return;
    }
    setRenamePending(true);
    setRenameError(null);
    try {
      await gpaApi.updateConversation(renameTarget.conversationId, { title });
      setRenameTarget(null);
      emitConversationsChanged();
    } catch (caught) {
      const message = failure(caught);
      if (message) setRenameError(message);
    } finally {
      setRenamePending(false);
    }
  }

  async function togglePin(conversation: ConversationSummary) {
    if (busyId) return;
    setBusyId(conversation.conversationId);
    setActionError(null);
    try {
      await gpaApi.updateConversation(conversation.conversationId, {
        pinned: !conversation.pinnedAt,
      });
      emitConversationsChanged();
    } catch (caught) {
      const message = failure(caught);
      if (message) setActionError(message);
    } finally {
      setBusyId(null);
    }
  }

  async function archive(conversation: ConversationSummary) {
    if (busyId) return;
    setBusyId(conversation.conversationId);
    setActionError(null);
    try {
      await gpaApi.updateConversation(conversation.conversationId, {
        archived: true,
      });
      if (activeId === conversation.conversationId) {
        navigate("/interact/text");
      }
      emitConversationsChanged();
    } catch (caught) {
      const message = failure(caught);
      if (message) setActionError(message);
    } finally {
      setBusyId(null);
    }
  }

  async function confirmDelete() {
    if (!deleteTarget || deletePending) return;
    setDeletePending(true);
    setDeleteError(null);
    try {
      await gpaApi.deleteConversation(deleteTarget.conversationId);
      if (activeId === deleteTarget.conversationId) {
        navigate("/interact/text");
      }
      setDeleteTarget(null);
      emitConversationsChanged();
    } catch (caught) {
      const message = failure(caught);
      if (message) setDeleteError(message);
    } finally {
      setDeletePending(false);
    }
  }

  function actionsFor(conversation: ConversationSummary): RowActionItem[] {
    const pinned = Boolean(conversation.pinnedAt);
    return [
      {
        icon: pinned ? <PinOff size={14} aria-hidden="true" /> : <Pin size={14} aria-hidden="true" />,
        text: pinned ? "取消置顶" : "置顶",
        disabled: busyId === conversation.conversationId,
        run: () => void togglePin(conversation),
      },
      {
        icon: <Pencil size={14} aria-hidden="true" />,
        text: "重命名",
        disabled: busyId === conversation.conversationId,
        run: () => openRename(conversation),
      },
      {
        icon: <Archive size={14} aria-hidden="true" />,
        text: "归档",
        disabled: busyId === conversation.conversationId,
        run: () => void archive(conversation),
      },
      {
        icon: <Trash2 size={14} aria-hidden="true" />,
        text: "删除",
        danger: true,
        disabled: busyId === conversation.conversationId,
        run: () => {
          setDeleteTarget(conversation);
          setDeleteError(null);
        },
      },
    ];
  }

  return (
    <div className="gpa-rail">
      <div className="gpa-rail__head">
        <span>最近会话</span>
        <Button
          variant="text"
          size="compact"
          onClick={() => navigate(newHref)}
          aria-label="新建会话"
        >
          <Plus size={14} aria-hidden="true" />
          新建
        </Button>
      </div>
      {actionError ? (
        <p className="gpa-rail__error" role="alert">
          {actionError}
        </p>
      ) : null}
      {loading ? (
        <div className="gpa-rail__loading">
          <Spinner label="加载会话" />
        </div>
      ) : conversations.length === 0 ? null : (
        <>
          <ul className="gpa-rail__list">
            {visible.map((conversation) => {
              const href = hrefFor(conversation);
              const active = activeId === conversation.conversationId;
              const title = titleOf(conversation);
              return (
                <li
                  key={conversation.conversationId}
                  className={`gpa-rail__row${active ? " is-active" : ""}`}
                >
                  <Link
                    className="gpa-rail__item"
                    to={href}
                    aria-current={active ? "page" : undefined}
                    title={title}
                  >
                    {conversation.pinnedAt ? (
                      <Pin
                        className="gpa-rail__pin"
                        size={12}
                        aria-label="已置顶"
                      />
                    ) : null}
                    <span className="gpa-rail__title">{title}</span>
                    {conversation.channels.includes("voice") ? (
                      <Mic
                        className="gpa-rail__voice"
                        size={12}
                        aria-label="含语音"
                      />
                    ) : null}
                    {conversation.runningTaskCount > 0 ? (
                      <span
                        className="gpa-rail__running"
                        title={`${conversation.runningTaskCount} 个任务运行中`}
                        aria-label={`${conversation.runningTaskCount} 个任务运行中`}
                      />
                    ) : null}
                    <span className="gpa-rail__time">
                      {relativeTime(conversation.updatedAt)}
                    </span>
                  </Link>
                  <RowActionsMenu
                    label={`更多操作：${title}`}
                    items={actionsFor(conversation)}
                  />
                </li>
              );
            })}
          </ul>
          {conversations.length > VISIBLE_LIMIT ? (
            <button
              type="button"
              className="gpa-rail__expand"
              onClick={() => setExpanded((current) => !current)}
            >
              {expanded
                ? "收起会话"
                : `展开其余 ${remaining} 个会话`}
            </button>
          ) : null}
        </>
      )}
      {remaining > 0 ? (
        <Link className="gpa-rail__all" to="/history">
          查看全部会话
        </Link>
      ) : null}

      <Dialog
        open={renameTarget !== null}
        title="重命名会话"
        closeLabel="关闭重命名"
        onClose={() => setRenameTarget(null)}
        closeDisabled={renamePending}
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => setRenameTarget(null)}
              disabled={renamePending}
            >
              取消
            </Button>
            <Button onClick={() => void saveRename()} loading={renamePending}>
              保存
            </Button>
          </>
        }
      >
        <Field
          label="会话名称"
          error={renameError ?? undefined}
          hint="用于在最近会话和历史会话中识别这段对话。"
        >
          <Input
            value={renameDraft}
            maxLength={80}
            onChange={(event) => {
              setRenameDraft(event.target.value);
              setRenameError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void saveRename();
              }
            }}
            data-dialog-initial-focus
          />
        </Field>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        title="删除会话？"
        closeLabel="关闭删除确认"
        onClose={() => setDeleteTarget(null)}
        closeDisabled={deletePending}
        footer={
          <>
            <span className="gpa-rail__dialog-note">删除后不可恢复。</span>
            <Button
              variant="secondary"
              onClick={() => setDeleteTarget(null)}
              disabled={deletePending}
            >
              取消
            </Button>
            <Button
              variant="danger"
              onClick={() => void confirmDelete()}
              loading={deletePending}
            >
              删除
            </Button>
          </>
        }
      >
        <p className="gpa-rail__dialog-copy">
          “{deleteTarget ? titleOf(deleteTarget) : ""}”及其轮次、任务和产物记录将被删除。
        </p>
        {deleteError ? (
          <p className="gpa-rail__dialog-error" role="alert">
            {deleteError}
          </p>
        ) : null}
      </Dialog>
    </div>
  );
}
