import {
  Archive,
  Database,
  Download,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { gpaApi, isAuthError, type TurnView } from "../gpaApi";
import {
  Alert,
  Button,
  Dialog,
  Field,
  Input,
  RowActionsMenu,
  type RowActionItem,
} from "../ui/index";

interface RailToggle {
  open: boolean;
  onToggle: () => void;
  label: string;
  icon: ReactNode;
  count: number;
  id: string;
}

/**
 * The shared conversation header actions: rail toggle, new conversation and
 * the overflow menu (memory/refresh/rename/export/archive/delete). Text and
 * voice keep the same action geometry; only the rail label and refresh verb
 * differ.
 */
export function ConversationActions({
  conversationId,
  title,
  turns,
  rail,
  newHref,
  newLabel = "新建会话",
  refreshLabel = "刷新",
  onRefresh,
  onRenamed,
  onArchived,
  onDeleted,
  onNotice,
  onError,
  onAuthRequired,
  disabled = false,
}: {
  conversationId: string;
  title: string;
  turns: TurnView[];
  rail: RailToggle;
  newHref: string;
  newLabel?: string;
  refreshLabel?: string;
  onRefresh: () => void | Promise<void>;
  onRenamed: () => void | Promise<void>;
  onArchived: () => void;
  onDeleted: () => void;
  onNotice: (message: string) => void;
  onError: (message: string) => void;
  onAuthRequired: () => void;
  disabled?: boolean;
}) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState<
    "memory" | "rename" | "archive" | "delete" | null
  >(null);
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  function fail(caught: unknown, fallback: string): string {
    if (isAuthError(caught)) {
      onAuthRequired();
      return "";
    }
    return caught instanceof Error ? caught.message : fallback;
  }

  async function saveMemory() {
    setBusy("memory");
    try {
      const result = await gpaApi.commitConversationMemory(conversationId);
      onNotice(result.message);
    } catch (caught) {
      const message = fail(caught, "保存记忆失败");
      if (message) onError(message);
    } finally {
      setBusy(null);
    }
  }

  function openRename() {
    setRenameDraft(title);
    setRenameError(null);
    setRenameOpen(true);
  }

  async function renameConversation() {
    const next = renameDraft.trim();
    if (!next) {
      setRenameError("会话名称不能为空。");
      return;
    }
    setBusy("rename");
    setRenameError(null);
    try {
      await gpaApi.updateConversation(conversationId, { title: next });
      await onRenamed();
      setRenameOpen(false);
    } catch (caught) {
      const message = fail(caught, "重命名会话失败");
      if (message) setRenameError(message);
    } finally {
      setBusy(null);
    }
  }

  async function archiveConversation() {
    setBusy("archive");
    try {
      await gpaApi.updateConversation(conversationId, { archived: true });
      onArchived();
    } catch (caught) {
      const message = fail(caught, "归档会话失败");
      if (message) onError(message);
    } finally {
      setBusy(null);
    }
  }

  async function deleteConversation() {
    setBusy("delete");
    setDeleteError(null);
    try {
      await gpaApi.deleteConversation(conversationId);
      onDeleted();
    } catch (caught) {
      const message = fail(caught, "删除会话失败");
      if (message) setDeleteError(message);
    } finally {
      setBusy(null);
    }
  }

  function exportConversation() {
    const rows = [`# ${title}`, ""];
    for (const turn of turns) {
      rows.push("## 你", "", turn.userText || "（无文本）", "");
      rows.push("## GPA", "", turn.assistantText || "（无回复）", "", "---", "");
    }
    const blob = new Blob([rows.join("\n")], {
      type: "text/markdown;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${title.replace(/[\\/:*?"<>|]/g, "-").slice(0, 60) || "conversation"}.md`;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    URL.revokeObjectURL(url);
  }

  const menuItems: RowActionItem[] = [
    {
      icon: <Database size={15} aria-hidden="true" />,
      text: "保存记忆",
      disabled: disabled || busy !== null,
      run: () => void saveMemory(),
    },
    {
      icon: <RefreshCw size={15} aria-hidden="true" />,
      text: refreshLabel,
      disabled: busy !== null,
      run: () => void onRefresh(),
    },
    {
      icon: <Pencil size={15} aria-hidden="true" />,
      text: "重命名会话",
      disabled: busy !== null,
      run: openRename,
    },
    {
      icon: <Download size={15} aria-hidden="true" />,
      text: "导出对话",
      disabled: busy !== null || turns.length === 0,
      run: exportConversation,
    },
    {
      icon: <Archive size={15} aria-hidden="true" />,
      text: "归档会话",
      disabled: busy !== null,
      run: () => void archiveConversation(),
    },
    {
      icon: <Trash2 size={15} aria-hidden="true" />,
      text: "永久删除会话",
      danger: true,
      disabled: busy !== null,
      run: () => {
        setDeleteError(null);
        setDeleteOpen(true);
      },
    },
  ];

  return (
    <>
      <Button
        variant="secondary"
        size="compact"
        className={`gpa-artifact-toggle${rail.open ? " is-active" : ""}`}
        aria-label={rail.label}
        aria-expanded={rail.open}
        aria-controls={rail.id}
        onClick={rail.onToggle}
      >
        {rail.icon}
        <span className="gpa-btn-label">{rail.label}</span>
        {rail.count > 0 ? (
          <span className="gpa-rail-toggle__count">{rail.count}</span>
        ) : null}
      </Button>
      <Button
        variant="secondary"
        size="compact"
        title="开启新的会话"
        aria-label={newLabel}
        onClick={() => navigate(newHref)}
      >
        <Plus size={14} aria-hidden="true" />
        <span className="gpa-btn-label">{newLabel}</span>
      </Button>
      <RowActionsMenu label="更多会话操作" items={menuItems} />

      <Dialog
        open={renameOpen}
        title="重命名会话"
        closeLabel="关闭"
        onClose={() => {
          if (busy !== "rename") setRenameOpen(false);
        }}
        closeDisabled={busy === "rename"}
        footer={
          <>
            <Button
              variant="secondary"
              size="compact"
              disabled={busy === "rename"}
              onClick={() => setRenameOpen(false)}
            >
              取消
            </Button>
            <Button
              size="compact"
              loading={busy === "rename"}
              onClick={() => void renameConversation()}
            >
              保存
            </Button>
          </>
        }
      >
        <Field label="会话名称">
          <Input
            value={renameDraft}
            maxLength={80}
            autoFocus
            data-dialog-initial-focus="true"
            onChange={(event) => {
              setRenameDraft(event.target.value);
              setRenameError(null);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                void renameConversation();
              }
            }}
          />
        </Field>
        {renameError ? <Alert tone="danger">{renameError}</Alert> : null}
      </Dialog>

      <Dialog
        open={deleteOpen}
        title="永久删除会话"
        closeLabel="关闭"
        onClose={() => {
          if (busy !== "delete") setDeleteOpen(false);
        }}
        closeDisabled={busy === "delete"}
        footer={
          <>
            <Button
              variant="secondary"
              size="compact"
              disabled={busy === "delete"}
              onClick={() => setDeleteOpen(false)}
            >
              取消
            </Button>
            <Button
              variant="danger"
              size="compact"
              loading={busy === "delete"}
              onClick={() => void deleteConversation()}
            >
              永久删除
            </Button>
          </>
        }
      >
        <p>
          “{title}”及其全部回合、任务与产物记录都会被删除，且无法恢复。
        </p>
        {deleteError ? <Alert tone="danger">{deleteError}</Alert> : null}
      </Dialog>
    </>
  );
}
