import {
  FileText,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  gpaApi,
  isAuthError,
  type KbEntry,
  type KbEntryPage,
} from "./gpaApi";
import {
  Alert,
  Button,
  Dialog,
  EmptyState,
  IconButton,
  Input,
  PageHeader,
  Spinner,
} from "./ui/index";
import "./knowledge.css";

const STATUS_LABELS: Record<string, string> = {
  pending: "排队中",
  indexing: "索引中",
  reconciling: "对账中",
  ready: "已就绪",
  failed: "失败",
  deleting: "删除中",
  deleted: "已删除",
  delete_failed: "删除失败",
};

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

function formatBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function messageOf(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function KnowledgePage({
  onAuthRequired,
  isAdmin,
}: {
  onAuthRequired: () => void;
  isAdmin: boolean;
}) {
  const [scope, setScope] = useState<"private" | "shared">("private");
  const [page, setPage] = useState<KbEntryPage | null>(null);
  const [deletions, setDeletions] = useState<KbEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadFiles, setUploadFiles] = useState<File[]>([]);
  const [category, setCategory] = useState("");
  const [uploading, setUploading] = useState(false);
  const [preview, setPreview] = useState<KbEntry | null>(null);
  const [previewText, setPreviewText] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<KbEntry | null>(null);
  const [deleting, setDeleting] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const loadSeqRef = useRef(0);
  const previewSeqRef = useRef(0);

  const load = useCallback(async () => {
    const seq = loadSeqRef.current + 1;
    loadSeqRef.current = seq;
    setLoading(true);
    setError(null);
    try {
      const [data, pendingDeletions] = await Promise.all([
        gpaApi.listKbEntries(scope, "", search),
        gpaApi.listKbDeletions(),
      ]);
      if (loadSeqRef.current !== seq) return;
      setPage(data);
      setDeletions(pendingDeletions.items ?? []);
    } catch (caught) {
      if (loadSeqRef.current !== seq) return;
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setError(messageOf(caught, "知识库加载失败"));
    } finally {
      if (loadSeqRef.current === seq) setLoading(false);
    }
  }, [onAuthRequired, scope, search]);

  useEffect(() => {
    void load();
  }, [load]);

  const canWrite = scope === "private" || isAdmin;
  const items = useMemo(
    () =>
      (page?.items ?? []).filter(
        (item) => !(scope === "private" && !item.docId && item.name === "uploads"),
      ),
    [page, scope],
  );
  const pendingCount = useMemo(
    () => items.filter((item) => item.status !== "ready").length,
    [items],
  );

  useEffect(() => {
    if (!items.some((item) => ["pending", "indexing", "reconciling", "deleting"].includes(item.status))) {
      return;
    }
    const timer = window.setTimeout(() => void load(), 4000);
    return () => window.clearTimeout(timer);
  }, [items, load]);

  async function openPreview(entry: KbEntry) {
    if (entry.isDir) return;
    const seq = previewSeqRef.current + 1;
    previewSeqRef.current = seq;
    setPreview(entry);
    setPreviewText("");
    setPreviewLoading(true);
    try {
      const data = await gpaApi.getKbContent(entry.scope, entry.uri);
      if (previewSeqRef.current !== seq) return;
      setPreviewText(data.content);
    } catch (caught) {
      if (previewSeqRef.current !== seq) return;
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setPreviewText(messageOf(caught, "该文件暂不支持文本预览，可下载后查看。"));
    } finally {
      if (previewSeqRef.current === seq) setPreviewLoading(false);
    }
  }

  async function submitUpload() {
    if (uploadFiles.length === 0) return;
    setUploading(true);
    setError(null);
    try {
      for (const file of uploadFiles) {
        await gpaApi.uploadFile(
          file,
          scope === "shared" ? "shared_kb" : "private_kb",
          category.trim() ? { categoryId: category.trim() } : {},
        );
      }
      setUploadFiles([]);
      setCategory("");
      setUploadOpen(false);
      await load();
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setError(messageOf(caught, "上传失败"));
    } finally {
      setUploading(false);
    }
  }

  async function deleteEntry() {
    if (!deleteTarget) return;
    setDeleting(true);
    try {
      await gpaApi.deleteKbEntry(deleteTarget.scope, deleteTarget.uri);
      setDeleteTarget(null);
      await load();
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setError(messageOf(caught, "删除失败"));
    } finally {
      setDeleting(false);
    }
  }

  async function retry(entry: KbEntry) {
    if (!entry.docId) return;
    try {
      if (entry.status === "delete_failed") {
        await gpaApi.retryDeleteKbDocument(entry.docId);
      } else {
        await gpaApi.reindexKbDocument(entry.docId);
      }
      await load();
    } catch (caught) {
      if (isAuthError(caught)) {
        onAuthRequired();
        return;
      }
      setError(messageOf(caught, "重试失败"));
    }
  }

  return (
    <div className="knowledge-page">
      <PageHeader
        title="知识库管理"
        actions={
          <div className="knowledge-page__actions">
            <Button variant="secondary" onClick={() => void load()} loading={loading}>
              <RefreshCw size={14} aria-hidden="true" />
              刷新
            </Button>
            {canWrite ? (
              <Button onClick={() => setUploadOpen(true)}>
                <Upload size={14} aria-hidden="true" />
                上传文件
              </Button>
            ) : null}
          </div>
        }
      />

      <div className="knowledge-page__toolbar">
        <div className="knowledge-tabs" role="tablist" aria-label="知识库范围">
          <button
            type="button"
            role="tab"
            aria-selected={scope === "private"}
            className={scope === "private" ? "is-active" : undefined}
            onClick={() => setScope("private")}
          >
            我的私有库
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={scope === "shared"}
            className={scope === "shared" ? "is-active" : undefined}
            onClick={() => setScope("shared")}
          >
            团队共享库
          </button>
        </div>
        <label className="knowledge-search">
          <Search size={14} aria-hidden="true" />
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索知识库"
            aria-label="搜索知识库"
          />
        </label>
      </div>

      {error ? <Alert tone="danger">{error}</Alert> : null}
      {deletions.length > 0 ? (
        <div className="knowledge-deletions" role="status">
          <div className="knowledge-deletions__title">
            有 {deletions.length} 个删除任务未完成
          </div>
          {deletions.map((entry) => (
            <div className="knowledge-deletions__row" key={entry.docId ?? entry.uri}>
              <span>
                {entry.name || entry.uri}
                <small>{statusLabel(entry.status)}</small>
              </span>
              {canWrite && entry.docId ? (
                <Button variant="text" size="compact" onClick={() => void retry(entry)}>
                  重试删除
                </Button>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}
      {page?.degraded?.includes("ov_unavailable") ? (
        <Alert tone="warning">OpenViking 暂时不可用，当前仅显示已入库的记录。</Alert>
      ) : null}

      {loading && !page ? (
        <Spinner label="加载知识库" />
      ) : !page || items.length === 0 ? (
        <EmptyState
          title={search ? "没有匹配的文件" : "知识库还是空的"}
          description={
            scope === "shared" && !isAdmin
              ? "共享库由管理员维护；这里会显示管理员上传的资料。"
              : "上传 PDF、Markdown、Office 文档或图片，索引完成后 Agent 就能检索到。"
          }
          action={
            canWrite ? (
              <Button onClick={() => setUploadOpen(true)}>上传第一份资料</Button>
            ) : undefined
          }
        />
      ) : (
        <div className="knowledge-table" role="table" aria-label="知识库文件">
          <div className="knowledge-table__head" role="row">
            <span role="columnheader">名称</span>
            <span role="columnheader">状态</span>
            <span role="columnheader">大小</span>
            <span role="columnheader">更新时间</span>
            <span role="columnheader">操作</span>
          </div>
          {items.map((entry) => (
            <div className="knowledge-table__row" role="row" key={entry.uri}>
              <span className="knowledge-table__name" role="cell">
                <FileText size={15} aria-hidden="true" />
                <span>
                  <strong>{entry.name}</strong>
                  {entry.path && entry.path !== entry.name ? <small>{entry.path}</small> : null}
                  {entry.error ? <small className="is-error">{entry.error}</small> : null}
                </span>
              </span>
              <span role="cell">
                <span className={`knowledge-status knowledge-status--${entry.status}`}>
                  {statusLabel(entry.status)}
                </span>
              </span>
              <span role="cell" className="knowledge-table__muted">
                {formatBytes(entry.size)}
              </span>
              <span role="cell" className="knowledge-table__muted">
                {entry.updatedAt ? new Date(entry.updatedAt).toLocaleString("zh-CN") : "—"}
              </span>
              <span className="knowledge-table__row-actions" role="cell">
                {!entry.isDir ? (
                  <Button variant="text" size="compact" onClick={() => void openPreview(entry)}>
                    预览
                  </Button>
                ) : null}
                {entry.downloadUrl ? (
                  <a className="knowledge-download" href={entry.downloadUrl}>
                    下载
                  </a>
                ) : null}
                {canWrite && (entry.status === "failed" || entry.status === "delete_failed") ? (
                  <Button variant="text" size="compact" onClick={() => void retry(entry)}>
                    重试
                  </Button>
                ) : null}
                {canWrite && entry.docId && entry.status !== "deleting" && entry.status !== "deleted" ? (
                  <IconButton
                    label={`删除 ${entry.name}`}
                    size="small"
                    onClick={() => setDeleteTarget(entry)}
                  >
                    <Trash2 size={14} aria-hidden="true" />
                  </IconButton>
                ) : null}
              </span>
            </div>
          ))}
        </div>
      )}

      {pendingCount > 0 ? (
        <p className="knowledge-page__hint">有 {pendingCount} 个文件正在索引或清理，页面会自动刷新。</p>
      ) : null}

      <Dialog
        open={uploadOpen}
        title={`上传到${scope === "shared" ? "团队共享库" : "我的私有库"}`}
        onClose={() => (uploading ? undefined : setUploadOpen(false))}
        closeDisabled={uploading}
        footer={
          <>
            <Button variant="secondary" onClick={() => setUploadOpen(false)} disabled={uploading}>
              取消
            </Button>
            <Button onClick={() => void submitUpload()} loading={uploading} disabled={uploadFiles.length === 0}>
              开始上传
            </Button>
          </>
        }
      >
        <div className="knowledge-upload">
          <input
            ref={fileInputRef}
            type="file"
            multiple
            accept=".pdf,.md,.markdown,.txt,.docx,.pptx,.xlsx,.csv,.json,.png,.jpg,.jpeg"
            hidden
            onChange={(event) => {
              setUploadFiles(Array.from(event.target.files ?? []));
              event.target.value = "";
            }}
          />
          <button type="button" className="knowledge-dropzone" onClick={() => fileInputRef.current?.click()}>
            <Upload size={22} aria-hidden="true" />
            <strong>{uploadFiles.length ? `已选择 ${uploadFiles.length} 个文件` : "选择文件"}</strong>
            <span>支持 PDF、Markdown、TXT、DOCX、PPTX、XLSX、CSV、JSON、PNG、JPG/JPEG，单文件 ≤50MB</span>
          </button>
          {uploadFiles.length > 0 ? (
            <ul className="knowledge-upload__list">
              {uploadFiles.map((file) => (
                <li key={`${file.name}:${file.size}`}>
                  <FileText size={13} aria-hidden="true" />
                  <span>{file.name}</span>
                  <small>{formatBytes(file.size)}</small>
                </li>
              ))}
            </ul>
          ) : null}
          <label className="knowledge-upload__category">
            <span>分类（可选，使用英文 ID）</span>
            <Input
              value={category}
              onChange={(event) => setCategory(event.target.value)}
              placeholder="例：policy、hr、ops"
              maxLength={40}
            />
          </label>
        </div>
      </Dialog>

      <Dialog
        open={preview !== null}
        title={preview?.name ?? "预览"}
        onClose={() => setPreview(null)}
        footer={
          preview?.downloadUrl ? (
            <a className="knowledge-download" href={preview.downloadUrl}>
              下载原文件
            </a>
          ) : undefined
        }
      >
        {previewLoading ? (
          <Spinner label="读取预览" />
        ) : (
          <pre className="knowledge-preview">{previewText || "没有可预览的文本，请下载原文件。"}</pre>
        )}
      </Dialog>

      <Dialog
        open={deleteTarget !== null}
        title="删除知识库文件"
        onClose={() => (deleting ? undefined : setDeleteTarget(null))}
        closeDisabled={deleting}
        footer={
          <>
            <Button variant="secondary" onClick={() => setDeleteTarget(null)} disabled={deleting}>
              取消
            </Button>
            <Button variant="danger" onClick={() => void deleteEntry()} loading={deleting}>
              确认删除
            </Button>
          </>
        }
      >
        <p>
          确定删除 <strong>{deleteTarget?.name}</strong> 吗？删除后 Agent 将不再检索到它；已有的会话附件不会被删除。
        </p>
      </Dialog>
    </div>
  );
}
