import { ChevronDown, Download, FileText, Folder, Search, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { gpaApi, isAuthError, type FileArtifact } from "./gpaApi";
import { ArtifactPanel } from "./interaction/ArtifactPanel";
import type { DerivedArtifact } from "./interaction/derive";
import {
  Alert,
  Button,
  Dialog,
  EmptyState,
  Field,
  IconButton,
  Input,
  PageHeader,
  Select,
  Spinner,
} from "./ui/index";

type FileKind =
  | "image"
  | "video"
  | "audio"
  | "pdf"
  | "slides"
  | "document"
  | "spreadsheet"
  | "archive"
  | "text"
  | "other";

const KIND_LABELS: Record<FileKind, string> = {
  image: "图片",
  video: "视频",
  audio: "音频",
  pdf: "PDF",
  slides: "演示",
  document: "文档",
  spreadsheet: "表格",
  archive: "压缩包",
  text: "文本",
  other: "文件",
};

const KIND_ORDER: FileKind[] = [
  "image",
  "video",
  "audio",
  "pdf",
  "slides",
  "document",
  "spreadsheet",
  "archive",
  "text",
  "other",
];

/** Native option popups size to the widest label, so a full conversation
 * title would blow the popup far past the select and read as misaligned. */
const MAX_OPTION_CHARS = 28;

function optionLabel(title: string): string {
  return title.length > MAX_OPTION_CHARS
    ? `${title.slice(0, MAX_OPTION_CHARS)}…`
    : title;
}

function fileKind(file: FileArtifact): FileKind {
  const mime = (file.mime ?? "").toLowerCase();
  const name = file.name.toLowerCase();
  if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg|bmp|heic|avif)$/.test(name)) {
    return "image";
  }
  if (mime.startsWith("video/") || /\.(mp4|mov|m4v|webm|avi|mkv)$/.test(name)) {
    return "video";
  }
  if (mime.startsWith("audio/") || /\.(mp3|wav|m4a|aac|flac|ogg)$/.test(name)) {
    return "audio";
  }
  if (mime === "application/pdf" || name.endsWith(".pdf")) return "pdf";
  if (/\.(pptx?|key)$/.test(name)) return "slides";
  if (/\.(docx?|rtf)$/.test(name)) return "document";
  if (/\.(xlsx?|csv|tsv)$/.test(name)) return "spreadsheet";
  if (/\.(zip|rar|7z|tar|gz|tgz|bz2)$/.test(name)) return "archive";
  if (
    mime.startsWith("text/") ||
    /\.(md|markdown|txt|json|jsonl|log|ya?ml|xml|html?|css|js|ts|tsx|py|sh)$/.test(name)
  ) {
    return "text";
  }
  return "other";
}

function formatBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = value >= 10 ? Math.round(value * 10) / 10 : Math.round(value * 100) / 100;
  return `${rounded} ${units[unit]}`;
}

function formatDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

interface FileGroup {
  conversationId: string;
  title: string;
  files: FileArtifact[];
  updatedAt: string;
  totalBytes: number;
  hasBytes: boolean;
}

function conversationTitle(files: FileArtifact[]): string {
  const titled = files.find((file) => (file.conversationTitle ?? "").trim());
  if (titled) return (titled.conversationTitle ?? "").trim();
  return `会话 ${files[0]?.conversationId.slice(0, 8) ?? ""}`;
}

function buildGroups(files: FileArtifact[]): FileGroup[] {
  const groups = new Map<string, FileGroup>();
  for (const file of files) {
    const existing = groups.get(file.conversationId);
    if (!existing) {
      groups.set(file.conversationId, {
        conversationId: file.conversationId,
        title: conversationTitle([file]),
        files: [file],
        updatedAt: file.createdAt,
        totalBytes: file.bytes ?? 0,
        hasBytes: file.bytes !== null,
      });
      continue;
    }
    existing.files.push(file);
    if (file.createdAt > existing.updatedAt) existing.updatedAt = file.createdAt;
    if (file.bytes !== null) {
      existing.totalBytes += file.bytes;
      existing.hasBytes = true;
    }
  }
  return [...groups.values()].sort((left, right) =>
    right.updatedAt.localeCompare(left.updatedAt),
  );
}

export function FilesPage({ onAuthRequired }: { onAuthRequired: () => void }) {
  const navigate = useNavigate();
  const [artifacts, setArtifacts] = useState<FileArtifact[] | null>(null);
  const [search, setSearch] = useState("");
  const [conversationId, setConversationId] = useState("");
  const [kind, setKind] = useState<FileKind | "">("");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState<FileArtifact | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FileArtifact | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let active = true;
    setArtifacts(null);
    setError(null);
    void gpaApi
      .listFiles("", 200)
      .then((response) => {
        if (active) setArtifacts(response.items);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (isAuthError(caught)) {
          onAuthRequired();
          return;
        }
        setError(caught instanceof Error ? caught.message : "文件加载失败");
      });
    return () => {
      active = false;
    };
  }, [onAuthRequired, reloadKey]);

  useEffect(() => {
    if (!conversationId) return;
    setExpanded((current) => {
      if (current.has(conversationId)) return current;
      const next = new Set(current);
      next.add(conversationId);
      return next;
    });
  }, [conversationId]);

  const allGroups = useMemo(() => buildGroups(artifacts ?? []), [artifacts]);
  const conversations = useMemo(
    () =>
      allGroups.map((group) => ({
        id: group.conversationId,
        title: group.title,
      })),
    [allGroups],
  );
  const availableKinds = useMemo(() => {
    const present = new Set<FileKind>();
    for (const file of artifacts ?? []) present.add(fileKind(file));
    return KIND_ORDER.filter((value) => present.has(value));
  }, [artifacts]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLocaleLowerCase();
    return (artifacts ?? []).filter(
      (file) =>
        (!needle || file.name.toLocaleLowerCase().includes(needle)) &&
        (!kind || fileKind(file) === kind) &&
        (!conversationId || file.conversationId === conversationId),
    );
  }, [artifacts, conversationId, kind, search]);
  const groups = useMemo(() => buildGroups(filtered), [filtered]);

  const previewArtifacts = useMemo<DerivedArtifact[]>(
    () =>
      preview
        ? [
            {
              artifactId: preview.artifactId,
              taskId: preview.taskId,
              name: preview.name,
              downloadUrl: preview.downloadUrl,
              createdAt: preview.createdAt,
              mime: preview.mime,
              bytes: preview.bytes,
            },
          ]
        : [],
    [preview],
  );

  function toggleGroup(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function clearFilters() {
    setSearch("");
    setKind("");
    setConversationId("");
  }

  async function requestDeletion() {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setFeedback(null);
    try {
      await gpaApi.deleteFile(deleteTarget.artifactId);
      setArtifacts(
        (current) =>
          current?.filter((file) => file.artifactId !== deleteTarget.artifactId) ??
          null,
      );
      setDeleteTarget(null);
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setFeedback("文件无法删除，请重试。");
      setDeleteTarget(null);
    } finally {
      setDeleting(false);
    }
  }

  const empty = artifacts !== null && artifacts.length === 0;

  return (
    <div className="gpa-files-page">
      <PageHeader title="我的文件" />

      <div className="gpa-files-toolbar" role="search" aria-label="文件筛选">
        <label className="ui-field">
          <span className="ui-field__label">搜索</span>
          <span className="gpa-files-search">
            <Search size={15} aria-hidden="true" />
            <Input
              type="search"
              placeholder="文件名"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </span>
        </label>
        <Field label="来源会话">
          <Select
            value={conversationId}
            onChange={(event) => setConversationId(event.target.value)}
          >
            <option value="">全部会话</option>
            {conversations.map((conversation) => (
              <option key={conversation.id} value={conversation.id}>
                {optionLabel(conversation.title)}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="文件类型">
          <Select
            value={kind}
            onChange={(event) => setKind(event.target.value as FileKind | "")}
          >
            <option value="">全部类型</option>
            {availableKinds.map((value) => (
              <option key={value} value={value}>
                {KIND_LABELS[value]}
              </option>
            ))}
          </Select>
        </Field>
      </div>

      {feedback ? <Alert tone="danger">{feedback}</Alert> : null}

      {error ? (
        <EmptyState
          className="ui-empty-state--page"
          title="文件无法加载。"
          description={error}
          action={
            <Button
              variant="secondary"
              onClick={() => setReloadKey((key) => key + 1)}
            >
              重试
            </Button>
          }
        />
      ) : null}

      {!error && artifacts === null ? (
        <div className="gpa-files-loading">
          <Spinner label="加载文件" />
          正在加载文件…
        </div>
      ) : null}

      {!error && empty ? (
        <EmptyState
          className="ui-empty-state--page"
          title="暂无成果文件。"
          description="任务生成的成果会保存在这里，方便下载和继续修改。"
          action={<Button onClick={() => navigate("/interact/text")}>创建新会话</Button>}
        />
      ) : null}

      {!error && artifacts && artifacts.length > 0 && groups.length === 0 ? (
        <EmptyState
          className="ui-empty-state--page"
          title="没有匹配的文件"
          description="试试其他文件名，或清除筛选查看全部成果。"
          action={
            <Button variant="secondary" onClick={clearFilters}>
              清除筛选
            </Button>
          }
        />
      ) : null}

      {groups.length > 0 ? (
        <ul className="gpa-files-list" aria-label="会话产物">
          {groups.map((group) => {
            const open = expanded.has(group.conversationId);
            const panelId = `gpa-files-group-${group.conversationId}`;
            return (
              <li className="gpa-file-group" key={group.conversationId}>
                <button
                  type="button"
                  className="gpa-file-group__summary"
                  aria-expanded={open}
                  aria-controls={open ? panelId : undefined}
                  onClick={() => toggleGroup(group.conversationId)}
                >
                  <span className="gpa-file-group__icon" aria-hidden="true">
                    <Folder size={18} />
                  </span>
                  <span className="gpa-file-group__heading">
                    <span className="gpa-file-group__title" title={group.title}>
                      {group.title}
                    </span>
                    <span className="gpa-file-group__meta">
                      {group.files.length} 个文件 · 共{" "}
                      {group.hasBytes ? formatBytes(group.totalBytes) : "—"} ·
                      更新于 {formatDate(group.updatedAt)}
                    </span>
                  </span>
                  <ChevronDown
                    size={18}
                    aria-hidden="true"
                    className="gpa-file-group__chevron"
                  />
                </button>

                {open ? (
                  <ul
                    className="gpa-file-group__files"
                    id={panelId}
                    aria-label={`${group.title} 的产物`}
                  >
                    {group.files.map((file) => (
                      <li className="gpa-file-row" key={file.artifactId}>
                        <span className="gpa-file-row__icon" aria-hidden="true">
                          <FileText size={16} />
                        </span>
                        <span className="gpa-file-row__body">
                          <span
                            className="gpa-file-row__name"
                            title={
                              file.taskGoal
                                ? `${file.name}\n${file.taskGoal}`
                                : file.name
                            }
                          >
                            {file.name}
                          </span>
                          <span className="gpa-file-row__meta">
                            <span>{KIND_LABELS[fileKind(file)]}</span>
                            <span>{formatBytes(file.bytes)}</span>
                            <time dateTime={file.createdAt}>
                              {formatDate(file.createdAt)}
                            </time>
                          </span>
                        </span>
                        <span className="gpa-file-row__actions">
                          <Button
                            variant="text"
                            size="compact"
                            onClick={() => setPreview(file)}
                          >
                            预览
                          </Button>
                          <Button
                            variant="secondary"
                            size="compact"
                            onClick={() =>
                              navigate(
                                `/interact/text/${encodeURIComponent(
                                  file.conversationId,
                                )}?artifactId=${encodeURIComponent(file.artifactId)}`,
                              )
                            }
                          >
                            继续修改
                          </Button>
                          <a
                            className="ui-icon-button ui-icon-button--small"
                            href={file.downloadUrl}
                            aria-label={`下载 ${file.name}`}
                            title={`下载 ${file.name}`}
                            download
                          >
                            <Download size={16} aria-hidden="true" />
                          </a>
                          <IconButton
                            size="small"
                            className="gpa-file-row__delete"
                            label={`删除 ${file.name}`}
                            onClick={() => setDeleteTarget(file)}
                          >
                            <Trash2 size={16} aria-hidden="true" />
                          </IconButton>
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            );
          })}
        </ul>
      ) : null}

      <Dialog
        open={preview !== null}
        className="gpa-files-preview-dialog"
        title="文件预览"
        closeLabel="关闭文件预览"
        onClose={() => setPreview(null)}
      >
        <div className="gpa-files-preview">
          <ArtifactPanel
            artifacts={previewArtifacts}
            selectedArtifactId={preview?.artifactId ?? null}
            onSelectArtifact={() => undefined}
          />
        </div>
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        title="删除文件？"
        closeLabel="关闭文件删除确认"
        closeDisabled={deleting}
        onClose={() => setDeleteTarget(null)}
        footer={
          deleteTarget ? (
            <>
              <Button
                data-dialog-initial-focus
                variant="secondary"
                onClick={() => setDeleteTarget(null)}
                disabled={deleting}
              >
                取消
              </Button>
              <Button
                variant="danger"
                loading={deleting}
                onClick={() => void requestDeletion()}
              >
                删除文件
              </Button>
            </>
          ) : null
        }
      >
        {deleteTarget ? (
          <p className="gpa-files-dialog-copy">
            删除 <strong>{deleteTarget.name}</strong>
            。文件会从该会话的成果中移除，且无法恢复。
          </p>
        ) : null}
      </Dialog>
    </div>
  );
}
