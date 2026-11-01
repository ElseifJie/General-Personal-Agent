import { Download, FileText } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Markdown } from "../components/Markdown";
import { Alert, Spinner } from "../ui/index";
import type { DerivedArtifact } from "./derive";

const MAX_PREVIEW_CHARS = 200_000;

type PreviewState =
  | { status: "loading" }
  | { status: "idle" }
  | {
      status: "ready";
      kind: "text";
      text: string;
      mime: string;
      size: number;
      truncated: boolean;
      markdown: boolean;
      json: boolean;
    }
  | { status: "ready"; kind: "image"; url: string; mime: string; size: number }
  | { status: "ready"; kind: "pdf"; url: string; mime: string; size: number }
  | { status: "ready"; kind: "binary"; mime: string; size: number }
  | { status: "error"; message: string };

function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes < 1) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function formatStamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function isTextName(name: string): boolean {
  return /\.(md|markdown|txt|json|jsonl|csv|log|yaml|yml|xml|html?|css|js|ts|tsx|py|sh)$/i.test(
    name,
  );
}

function isMarkdownName(name: string): boolean {
  return /\.(md|markdown)$/i.test(name);
}

function isJsonName(name: string): boolean {
  return /\.(json)$/i.test(name);
}

function isTextMime(mime: string): boolean {
  return (
    mime.startsWith("text/") ||
    /(json|xml|csv|yaml|markdown|x-ndjson)/i.test(mime)
  );
}

function prettifyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2);
  } catch {
    return text;
  }
}

/**
 * The artifact picker and preview shared by the text product rail and the
 * voice detail rail. Selection defaults to the newest artifact; an explicit
 * selection (dropdown or timeline "view") wins until it disappears.
 */
export function ArtifactPanel({
  artifacts,
  selectedArtifactId,
  onSelectArtifact,
}: {
  artifacts: DerivedArtifact[];
  selectedArtifactId: string | null;
  onSelectArtifact: (artifactId: string) => void;
}) {
  const active = useMemo(
    () =>
      artifacts.find((item) => item.artifactId === selectedArtifactId) ??
      artifacts[artifacts.length - 1] ??
      null,
    [artifacts, selectedArtifactId],
  );
  const [preview, setPreview] = useState<PreviewState>({ status: "loading" });

  useEffect(() => {
    if (!active) {
      setPreview({ status: "idle" });
      return;
    }
    if (!active.downloadUrl) {
      setPreview({ status: "idle" });
      return;
    }
    let cancelled = false;
    let objectUrl: string | null = null;
    const declaredMime = (active.mime ?? "").toLowerCase();
    const imageByName = /\.(png|jpe?g|gif|webp|svg|bmp|avif)$/i.test(
      active.name,
    );
    const pdfByName = /\.pdf$/i.test(active.name);
    // Skip the network round-trip for formats this panel cannot render; large
    // binaries would otherwise be downloaded in full before being rejected.
    if (
      !declaredMime.startsWith("image/") &&
      !imageByName &&
      declaredMime !== "application/pdf" &&
      !pdfByName &&
      !isTextMime(declaredMime) &&
      !isTextName(active.name)
    ) {
      setPreview({
        status: "ready",
        kind: "binary",
        mime: declaredMime,
        size: active.bytes ?? 0,
      });
      return;
    }
    setPreview({ status: "loading" });
    void (async () => {
      const response = await fetch(active.downloadUrl!, {
        credentials: "same-origin",
      });
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`.trim());
      }
      const blob = await response.blob();
      if (cancelled) return;
      const mime =
        response.headers.get("content-type")?.split(";")[0]?.trim() || "";
      const name = active.name;
      const size = blob.size;
      if (mime.startsWith("image/") || /\.(png|jpe?g|gif|webp|svg)$/i.test(name)) {
        objectUrl = URL.createObjectURL(blob);
        setPreview({ status: "ready", kind: "image", url: objectUrl, mime, size });
        return;
      }
      if (mime === "application/pdf" || /\.pdf$/i.test(name)) {
        objectUrl = URL.createObjectURL(blob);
        setPreview({ status: "ready", kind: "pdf", url: objectUrl, mime, size });
        return;
      }
      if (isTextMime(mime) || isTextName(name)) {
        const raw = await blob.text();
        if (cancelled) return;
        const truncated = raw.length > MAX_PREVIEW_CHARS;
        const text = truncated ? raw.slice(0, MAX_PREVIEW_CHARS) : raw;
        setPreview({
          status: "ready",
          kind: "text",
          text: isJsonName(name) ? prettifyJson(text) : text,
          mime,
          size,
          truncated,
          markdown: isMarkdownName(name) || /markdown/i.test(mime),
          json: isJsonName(name) || /json/i.test(mime),
        });
        return;
      }
      setPreview({ status: "ready", kind: "binary", mime, size });
    })().catch((error: unknown) => {
      if (cancelled) return;
      setPreview({
        status: "error",
        message: error instanceof Error ? error.message : "未知错误",
      });
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [active]);

  if (!active) {
    return (
      <p className="gpa-rail-empty">
        还没有产物。任务生成的文件会出现在这里。
      </p>
    );
  }

  return (
    <>
      {artifacts.length > 1 ? (
        <label className="gpa-artifact-picker">
          <span>当前产物</span>
          <select
            className="ui-select"
            value={active.artifactId}
            onChange={(event) => onSelectArtifact(event.target.value)}
          >
            {[...artifacts].reverse().map((artifact) => (
              <option key={artifact.artifactId} value={artifact.artifactId}>
                {artifact.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}

      <article className="gpa-artifact-detail">
        <header className="gpa-artifact-detail__head">
          <div className="gpa-artifact-detail__copy">
            <h3 title={active.name}>{active.name}</h3>
            <p>
              {[
                active.mime,
                formatBytes(active.bytes),
                active.createdAt ? formatStamp(active.createdAt) : "",
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </div>
          {active.downloadUrl ? (
            <a
              className="gpa-artifact-detail__download"
              href={active.downloadUrl}
              download
            >
              <Download size={13} aria-hidden="true" />
              下载
            </a>
          ) : null}
        </header>

        <div className="gpa-artifact-preview">
          {preview.status === "loading" ? (
            <div className="gpa-artifact-preview__loading">
              <Spinner label="加载产物预览" size={16} />
              正在加载预览…
            </div>
          ) : preview.status === "idle" ? (
            <p className="gpa-rail-empty">该产物暂时没有可用的下载地址。</p>
          ) : preview.status === "error" ? (
            <Alert tone="danger">预览加载失败：{preview.message}</Alert>
          ) : preview.kind === "image" ? (
            <img src={preview.url} alt={active.name} />
          ) : preview.kind === "pdf" ? (
            <iframe src={preview.url} title={active.name} />
          ) : preview.kind === "text" ? (
            <>
              {preview.markdown ? (
                <div className="gpa-artifact-markdown">
                  <Markdown breaks>{preview.text}</Markdown>
                </div>
              ) : (
                <pre className="gpa-artifact-code">{preview.text}</pre>
              )}
              {preview.truncated ? (
                <p className="gpa-artifact-preview__note">
                  预览已截断，仅显示前 20 万字符，完整内容请下载查看。
                </p>
              ) : null}
            </>
          ) : (
            <div className="gpa-artifact-binary">
              <FileText size={22} aria-hidden="true" />
              <p>该类型暂不支持在线预览，请下载查看。</p>
            </div>
          )}
        </div>
      </article>
    </>
  );
}
