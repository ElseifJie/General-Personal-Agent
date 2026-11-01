import {
  FileText,
  FolderOpen,
  Info,
  Paperclip,
  Send,
  Square,
  TriangleAlert,
  X,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Button, IconButton } from "../ui/index";

const ACCEPTED_FILE_TYPES =
  ".pdf,.md,.markdown,.txt,.docx,.pptx,.xlsx,.csv,.json,.png,.jpg,.jpeg";

function draftKey(conversationId?: string | null) {
  return `gpa.draft.${conversationId || "new"}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export function ConversationComposer({
  conversationId,
  running,
  sending,
  onSend,
  onUploadFiles,
  onStop,
  disabled = false,
  fileTools = false,
  autoFocus = false,
  variant = "default",
  placeholder = "继续对话，或补充新的要求…",
}: {
  conversationId?: string | null;
  running: boolean;
  sending: boolean;
  onSend: (text: string, attachmentIds?: string[]) => Promise<boolean>;
  onUploadFiles?: (files: File[]) => Promise<string[]>;
  onStop: () => Promise<void>;
  disabled?: boolean;
  /** Shows the workbench-style file entry points on new conversations. */
  fileTools?: boolean;
  /** Focus the input when the surface opens (new conversations only). */
  autoFocus?: boolean;
  /** `hero` is the taller first-message composer on the new-conversation page. */
  variant?: "default" | "hero";
  placeholder?: string;
}) {
  const key = draftKey(conversationId);
  const navigate = useNavigate();
  const [text, setText] = useState(() => {
    try {
      return sessionStorage.getItem(key) ?? "";
    } catch {
      return "";
    }
  });
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const latestText = useRef(text);
  latestText.current = text;
  /** Incremented on every user edit; a late send must not clear newer input. */
  const textVersionRef = useRef(0);
  const filesVersionRef = useRef(0);
  const [files, setFiles] = useState<File[]>([]);
  const [attachmentError, setAttachmentError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    let restored = "";
    try {
      restored = sessionStorage.getItem(key) ?? "";
    } catch {
      restored = "";
    }
    latestText.current = restored;
    textVersionRef.current += 1;
    setText(restored);
    if (autoFocus) textareaRef.current?.focus();
  }, [autoFocus, key]);

  useEffect(() => {
    try {
      if (text) sessionStorage.setItem(key, text);
      else sessionStorage.removeItem(key);
    } catch {
      // Drafts are a convenience; private mode may deny storage.
    }
  }, [key, text]);

  // Attachments belong to a single conversation; never carry an upload or a
  // stale selection across a conversation/draft-key switch.
  useEffect(() => {
    filesVersionRef.current += 1;
    setFiles([]);
    setAttachmentError(null);
    setUploading(false);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }, [conversationId, key]);

  async function submit() {
    const content = latestText.current.trim();
    if (!content || running || sending || disabled || uploading) return;
    const submittedConversationId = conversationId;
    const submittedKey = key;
    const submittedTextVersion = textVersionRef.current;
    const submittedFilesVersion = filesVersionRef.current;
    let attachmentIds: string[] = [];
    if (files.length > 0) {
      if (!onUploadFiles) {
        setAttachmentError("当前入口不支持上传附件。");
        return;
      }
      setUploading(true);
      setAttachmentError(null);
      try {
        attachmentIds = await onUploadFiles(files);
      } catch (caught) {
        setAttachmentError(caught instanceof Error ? caught.message : "附件上传失败");
        return;
      } finally {
        setUploading(false);
      }
    }
    let ok = false;
    try {
      ok = await onSend(content, attachmentIds);
    } catch {
      // A rejected send is a failed send: the draft stays for a retry.
      return;
    }
    if (!ok) return;
    // A late completion from conversation A must not clear conversation B or
    // text/files the user edited while the request was in flight.
    if (conversationId !== submittedConversationId || key !== submittedKey) return;
    if (
      textVersionRef.current !== submittedTextVersion ||
      filesVersionRef.current !== submittedFilesVersion
    ) {
      return;
    }
    textVersionRef.current += 1;
    filesVersionRef.current += 1;
    setText("");
    setFiles([]);
    setAttachmentError(null);
    latestText.current = "";
    try {
      sessionStorage.removeItem(key);
    } catch {
      // ignore
    }
  }

  function addFiles(selected: FileList | null) {
    const incoming = Array.from(selected ?? []);
    if (incoming.length === 0) return;
    filesVersionRef.current += 1;
    setFiles((current) => {
      const seen = new Set(
        current.map((file) => `${file.name}:${file.size}:${file.lastModified}`),
      );
      const next = [...current];
      for (const file of incoming) {
        const identity = `${file.name}:${file.size}:${file.lastModified}`;
        if (!seen.has(identity)) {
          seen.add(identity);
          next.push(file);
        }
      }
      return next;
    });
    setAttachmentError(null);
  }

  function removeFile(index: number) {
    filesVersionRef.current += 1;
    setFiles((current) => current.filter((_, currentIndex) => currentIndex !== index));
    setAttachmentError(null);
  }

  const hero = variant === "hero";

  return (
    <form
      className={`gpa-composer${hero ? " gpa-composer--hero" : ""}${
        running ? " is-running" : ""
      }`}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <label className="sr-only" htmlFor="gpa-composer-input">
        输入消息
      </label>
      <textarea
        id="gpa-composer-input"
        ref={textareaRef}
        value={text}
        onChange={(event) => {
          textVersionRef.current += 1;
          setText(event.target.value);
        }}
        placeholder={placeholder}
        rows={hero ? 3 : 2}
        maxLength={32_000}
        disabled={disabled}
        aria-label="输入消息"
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
      />
      {fileTools && files.length > 0 ? (
        <div className="gpa-composer__attachments">
          <ul className="gpa-attachments" aria-label="待上传文件">
            {files.map((file, index) => (
              <li
                className="gpa-attachment"
                key={`${file.name}:${file.size}:${file.lastModified}`}
              >
                <FileText size={13} aria-hidden="true" />
                <span className="gpa-attachment__name" title={file.name}>
                  {file.name}
                </span>
                <span className="gpa-attachment__size">
                  {formatBytes(file.size)}
                </span>
                <button
                  type="button"
                  className="gpa-attachment__remove"
                  aria-label={`移除 ${file.name}`}
                  onClick={() => removeFile(index)}
                >
                  <X size={13} aria-hidden="true" />
                </button>
              </li>
            ))}
          </ul>
          <p
            className={
              attachmentError
                ? "gpa-composer__attachment-error"
                : "gpa-composer__attachment-note"
            }
            role={attachmentError ? "alert" : undefined}
          >
            {attachmentError ? (
              <TriangleAlert size={12} aria-hidden="true" />
            ) : (
              <Info size={12} aria-hidden="true" />
            )}
            {attachmentError
              ? attachmentError
              : uploading
                ? "正在上传附件…"
                : "附件会在发送时上传，并随消息一起提交。"}
          </p>
        </div>
      ) : null}
      <div className="gpa-composer__footer">
        <span className="gpa-composer__hint" aria-live="polite">
          {running
            ? "GPA 正在回复，可停止本轮"
            : disabled
              ? "当前会话不可发送"
              : "Enter 发送 · Shift+Enter 换行"}
        </span>
        <div className="gpa-composer__actions">
          {fileTools ? (
            <>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ACCEPTED_FILE_TYPES}
                hidden
                onChange={(event) => {
                  addFiles(event.target.files);
                  event.target.value = "";
                }}
              />
              <Button
                variant="text"
                size="compact"
                title="添加附件"
                onClick={() => fileInputRef.current?.click()}
              >
                <Paperclip size={14} aria-hidden="true" />
                添加文件
              </Button>
              <Button
                variant="text"
                size="compact"
                onClick={() => navigate("/knowledge")}
              >
                <FolderOpen size={14} aria-hidden="true" />
                知识库
              </Button>
            </>
          ) : null}
          {running ? (
            <Button variant="secondary" size="compact" onClick={() => void onStop()}>
              <Square size={13} aria-hidden="true" />
              停止
            </Button>
          ) : (
            <IconButton
              label="发送"
              className="gpa-send"
              disabled={disabled || !text.trim() || sending || uploading}
              aria-busy={sending || undefined}
              onClick={() => void submit()}
            >
              <Send size={16} aria-hidden="true" />
            </IconButton>
          )}
        </div>
      </div>
    </form>
  );
}
