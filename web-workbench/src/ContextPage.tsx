import {
  Check,
  ChevronRight,
  Copy,
  FileText,
  Folder,
  FolderOpen,
  Lock,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from "react";

import { Markdown } from "./components/Markdown";
import { SourceView } from "./context/SourceView";
import {
  ancestorUris,
  buildContextTree,
  defaultCollapsed,
  defaultSelection,
  formatBytes,
  formatLabel,
  formatStamp,
  isFileVisible,
  isMarkdown,
  prettyContent,
  scopeLabel,
  searchFiles,
  type ContextTreeNode,
} from "./context/tree";
import { GpaApiError, gpaApi, type ContextNode, type ContextTree } from "./gpaApi";
import { Alert, Button, IconButton, Spinner } from "./ui/index";

type ViewTab = "detail" | "preview" | "source";

const TABS: ReadonlyArray<{ key: ViewTab; label: string }> = [
  { key: "detail", label: "详情" },
  { key: "preview", label: "预览" },
  { key: "source", label: "源码" },
];

const MAX_CONTENT_CHARS = 400_000;

function errorText(error: unknown, fallback: string): string {
  if (error instanceof GpaApiError) {
    return error.status === 401 ? "登录已过期，请重新登录。" : error.message;
  }
  return error instanceof Error && error.message ? error.message : fallback;
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // Fall through to the selection-based copy below.
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    const copied = document.execCommand("copy");
    area.remove();
    return copied;
  } catch {
    return false;
  }
}

/* -------------------------------------------------------------------------- */
/* Tree                                                                       */
/* -------------------------------------------------------------------------- */

interface TreeProps {
  nodes: ContextTreeNode[];
  depth: number;
  collapsed: ReadonlySet<string>;
  selectedUri: string | null;
  onToggle: (uri: string) => void;
  onSelect: (node: ContextNode) => void;
}

function TreeBranch({
  nodes,
  depth,
  collapsed,
  selectedUri,
  onToggle,
  onSelect,
}: TreeProps) {
  return (
    <>
      {nodes.map(({ node, description, children }) => {
        if (node.isDir) {
          const open = !collapsed.has(node.uri);
          return (
            <li key={node.uri}>
              <button
                type="button"
                className={`context-browser__row context-browser__row--dir${depth === 0 ? " context-browser__row--root" : ""}`}
                aria-expanded={open}
                title={node.uri}
                onClick={() => onToggle(node.uri)}
              >
                <ChevronRight
                  className={`context-browser__chevron${open ? " is-open" : ""}`}
                  size={12}
                  strokeWidth={2}
                  aria-hidden="true"
                />
                {open ? (
                  <FolderOpen
                    className="context-browser__icon"
                    size={depth === 0 ? 16 : 15}
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                ) : (
                  <Folder
                    className="context-browser__icon"
                    size={depth === 0 ? 16 : 15}
                    strokeWidth={1.75}
                    aria-hidden="true"
                  />
                )}
                <span className="context-browser__row-label">{node.name}</span>
                {description ? (
                  <span className="context-browser__row-note">{description}</span>
                ) : null}
              </button>
              {open ? (
                <div className="context-browser__children">
                  {children.length > 0 ? (
                    <ul className="context-browser__list">
                      <TreeBranch
                        nodes={children}
                        depth={depth + 1}
                        collapsed={collapsed}
                        selectedUri={selectedUri}
                        onToggle={onToggle}
                        onSelect={onSelect}
                      />
                    </ul>
                  ) : (
                    <p className="context-browser__empty-dir">该文件夹为空</p>
                  )}
                </div>
              ) : null}
            </li>
          );
        }
        const active = node.uri === selectedUri;
        return (
          <li key={node.uri}>
            <button
              type="button"
              className={`context-browser__row context-browser__row--file${active ? " is-active" : ""}`}
              aria-current={active ? "true" : undefined}
              title={node.uri}
              onClick={() => onSelect(node)}
            >
              <span className="context-browser__chevron-space" aria-hidden="true" />
              <FileText
                className="context-browser__icon"
                size={14}
                strokeWidth={1.75}
                aria-hidden="true"
              />
              <span className="context-browser__row-label context-browser__row-label--file">
                {node.name}
              </span>
            </button>
          </li>
        );
      })}
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export function ContextPage({ onAuthRequired }: { onAuthRequired: () => void }) {
  const [tree, setTree] = useState<ContextTree | null>(null);
  const [loading, setLoading] = useState(true);
  const [treeError, setTreeError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [search, setSearch] = useState("");
  const [selectedUri, setSelectedUri] = useState<string | null>(null);
  const [content, setContent] = useState<string | null>(null);
  const [contentTruncated, setContentTruncated] = useState(false);
  const [contentError, setContentError] = useState<string | null>(null);
  const [tab, setTab] = useState<ViewTab>("source");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [copyState, setCopyState] = useState<{
    kind: "path" | "content";
    ok: boolean;
  } | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setTreeError(null);
    void gpaApi
      .getContextTree()
      .then((response) => {
        if (!active) return;
        setTree(response);
        setSelectedUri((current) =>
          current && isFileVisible(response, current)
            ? current
            : defaultSelection(response),
        );
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof GpaApiError && caught.status === 401) {
          onAuthRequired();
          return;
        }
        setTreeError(errorText(caught, "上下文目录加载失败"));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [onAuthRequired, refreshToken]);

  const roots = useMemo(() => buildContextTree(tree), [tree]);

  const selected = useMemo(
    () =>
      (tree?.items ?? []).find(
        (item) => item.uri === selectedUri && !item.isDir,
      ) ?? null,
    [tree, selectedUri],
  );

  useEffect(() => {
    if (!selectedUri) return;
    document
      .querySelector<HTMLElement>(".context-browser__row.is-active")
      ?.scrollIntoView({ block: "nearest" });
  }, [collapsed, selectedUri]);

  useEffect(() => {
    if (!tree) return;
    setCollapsed(defaultCollapsed(roots, selectedUri, tree.userUri));
    // Only re-derive the default disclosure state when the listing changes;
    // manual expand/collapse must survive re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roots]);

  useEffect(() => {
    if (!selectedUri) return;
    const keys = ancestorUris(roots, selectedUri);
    if (keys.length === 0) return;
    setCollapsed((current) => {
      let changed = false;
      const next = new Set(current);
      for (const key of keys) {
        if (next.delete(key)) changed = true;
      }
      return changed ? next : current;
    });
  }, [roots, selectedUri]);

  useEffect(() => {
    if (!selected) {
      setContent(null);
      setContentTruncated(false);
      setContentError(null);
      return;
    }
    let active = true;
    setContent(null);
    setContentTruncated(false);
    setContentError(null);
    void gpaApi
      .getContextContent(selected.uri)
      .then((response) => {
        if (!active) return;
        setContent(response.content);
        setContentTruncated(response.truncated);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof GpaApiError && caught.status === 401) {
          onAuthRequired();
          return;
        }
        setContentError(errorText(caught, "内容加载失败"));
      });
    return () => {
      active = false;
    };
  }, [onAuthRequired, refreshToken, selected]);

  useEffect(() => {
    if (!copyState) return;
    const handle = window.setTimeout(() => setCopyState(null), 1800);
    return () => window.clearTimeout(handle);
  }, [copyState]);

  // "/" focuses the file search, the way a document browser's search shortcut
  // behaves. Ignored while typing or while a dialog owns the focus.
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey) {
        return;
      }
      const target = event.target as HTMLElement | null;
      if (
        target?.closest("input, textarea, select, [contenteditable='true']") ||
        document.querySelector("dialog[open], [role='dialog']")
      ) {
        return;
      }
      event.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const query = search.trim();
  const searching = query.length > 0;
  const results = useMemo(
    () => (searching ? searchFiles(roots, query) : []),
    [roots, query, searching],
  );
  const fileCount = useMemo(
    () => (tree?.items ?? []).filter((item) => !item.isDir).length,
    [tree],
  );

  const charCount = content ? Array.from(content).length : 0;
  const lineCount = content ? content.split("\n").length : 0;
  const stamp = formatStamp(selected?.updatedAt);
  const markdown = selected ? isMarkdown(selected.uri) : false;
  const outline = useMemo(() => {
    if (!content || !markdown) return [];
    return content.split("\n").flatMap((line) => {
      const hit = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      return hit ? [{ level: hit[1]!.length, text: hit[2]! }] : [];
    });
  }, [content, markdown]);

  const pathCopied = copyState?.kind === "path" && copyState.ok;
  const contentCopied = copyState?.kind === "content" && copyState.ok;

  function toggleFolder(uri: string) {
    setCollapsed((current) => {
      const next = new Set(current);
      if (next.has(uri)) next.delete(uri);
      else next.add(uri);
      return next;
    });
  }

  function selectFile(node: ContextNode) {
    setSelectedUri(node.uri);
  }

  function refresh() {
    setRefreshToken((current) => current + 1);
  }

  function clearSearch() {
    setSearch("");
    searchRef.current?.focus();
  }

  async function copy(kind: "path" | "content", value: string) {
    const ok = await writeClipboard(value);
    setCopyState({ kind, ok });
  }

  function handleTabKeys(event: ReactKeyboardEvent<HTMLDivElement>) {
    const focusedId = (event.target as HTMLElement | null)
      ?.closest<HTMLElement>('[role="tab"]')?.id;
    const focusedKey =
      TABS.find((entry) => focusedId === `context-tab-${entry.key}`)?.key ?? tab;
    const index = TABS.findIndex((entry) => entry.key === focusedKey);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    else return;
    event.preventDefault();
    const entry = TABS[next]!;
    setTab(entry.key);
    document.getElementById(`context-tab-${entry.key}`)?.focus();
  }

  const selectedUriValue = selected?.uri ?? "";
  const slash = selectedUriValue.lastIndexOf("/");
  const dirPart = slash >= 0 ? selectedUriValue.slice(0, slash + 1) : "";
  const filePart = slash >= 0 ? selectedUriValue.slice(slash + 1) : selectedUriValue;

  function renderTree() {
    if (!tree) {
      if (treeError) return null;
      return (
        <div className="context-browser__loading">
          <Spinner label="加载上下文目录" size={16} />
          <span>正在读取 OpenViking 目录…</span>
        </div>
      );
    }
    if (searching) {
      return (
        <div className="context-browser__results">
          <p className="context-browser__results-head">
            <span>搜索结果</span>
            <span>{results.length}</span>
          </p>
          {results.length === 0 ? (
            <p className="context-browser__results-state">
              没有匹配“{query}”的上下文文件。
            </p>
          ) : (
            <ul className="context-browser__list">
              {results.map((item) => {
                const active = item.uri === selectedUri;
                return (
                  <li key={item.uri}>
                    <button
                      type="button"
                      className={`context-browser__row context-browser__row--result${active ? " is-active" : ""}`}
                      aria-current={active ? "true" : undefined}
                      title={item.uri}
                      onClick={() => selectFile(item)}
                    >
                      <FileText
                        className="context-browser__icon"
                        size={14}
                        strokeWidth={1.75}
                        aria-hidden="true"
                      />
                      <span className="context-browser__result-copy">
                        <span className="context-browser__result-name">
                          {item.name}
                        </span>
                        <span className="context-browser__result-path">
                          {item.uri}
                        </span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      );
    }
    if (roots.length === 0) {
      return (
        <div className="context-browser__tree-empty">
          <FileText size={20} aria-hidden="true" />
          <p>没有可见的上下文目录</p>
          <span>当前账号在 OpenViking 中还没有任何目录。</span>
        </div>
      );
    }
    return (
      <nav aria-label="上下文目录">
        <ul className="context-browser__list">
          <TreeBranch
            nodes={roots}
            depth={0}
            collapsed={collapsed}
            selectedUri={selectedUri}
            onToggle={toggleFolder}
            onSelect={selectFile}
          />
        </ul>
      </nav>
    );
  }

  function renderDocument(): ReactNode {
    if (!tree && loading) {
      return (
        <div className="context-browser__blank">
          <Spinner label="加载上下文" size={18} />
          <p>正在读取上下文…</p>
        </div>
      );
    }
    if (!tree) {
      return (
        <div className="context-browser__blank">
          <FileText size={22} aria-hidden="true" />
          <p>上下文目录暂时不可用</p>
          <span>上游 OpenViking 返回错误；修复后可在左侧点刷新重新读取。</span>
        </div>
      );
    }
    if (!selected) {
      const empty = searching
        ? {
            title: "没有匹配的上下文文件",
            hint: "换个关键词，或清除搜索后继续浏览目录。",
          }
        : fileCount > 0
          ? {
              title: "选择一个上下文文件",
              hint: "支持 Markdown 预览、JSON 格式化与带行号的源码。",
            }
          : {
              title: "当前账号还没有可见的上下文文件",
              hint: "OpenViking 中的记忆、会话、配置与共享知识会按账号权限显示。",
            };
      return (
        <div className="context-browser__blank">
          <FileText size={22} aria-hidden="true" />
          <p>{empty.title}</p>
          <span>{empty.hint}</span>
        </div>
      );
    }

    const pending = content === null && !contentError;
    return (
      <div className="context-browser__card">
        <div className="context-browser__card-head">
          <div
            className="context-browser__tabs"
            role="tablist"
            aria-label="查看方式"
            onKeyDown={handleTabKeys}
          >
            {TABS.map((entry) => (
              <button
                key={entry.key}
                type="button"
                role="tab"
                id={`context-tab-${entry.key}`}
                aria-selected={tab === entry.key}
                aria-controls="context-doc-panel"
                tabIndex={tab === entry.key ? 0 : -1}
                className="context-browser__tab"
                onClick={() => setTab(entry.key)}
              >
                {entry.label}
              </button>
            ))}
          </div>
          <div className="context-browser__card-actions">
            <IconButton
              label={contentCopied ? "已复制内容" : "复制内容"}
              className="context-browser__icon-button"
              disabled={content === null}
              onClick={() => void copy("content", content ?? "")}
            >
              {contentCopied ? (
                <Check size={15} aria-hidden="true" />
              ) : (
                <Copy size={15} aria-hidden="true" />
              )}
            </IconButton>
          </div>
        </div>

        <div
          className="context-browser__pane"
          id="context-doc-panel"
          role="tabpanel"
          aria-labelledby={`context-tab-${tab}`}
        >
          {contentError ? (
            <div className="context-browser__error">
              <Alert tone="warning">{contentError}</Alert>
              <Button variant="secondary" size="compact" onClick={refresh}>
                重试
              </Button>
            </div>
          ) : pending ? (
            <div className="context-browser__loading">
              <Spinner label="读取上下文文件" size={16} />
              <span>正在读取文件…</span>
            </div>
          ) : (
            <>
              {contentTruncated ? (
                <p className="context-browser__truncated">
                  文件较大，仅显示前 {MAX_CONTENT_CHARS.toLocaleString("zh-CN")} 个字符。
                </p>
              ) : null}
              {tab === "detail" ? (
                <div className="context-browser__detail">
                  <dl className="context-browser__facts">
                    <div className="context-browser__fact">
                      <dt>路径</dt>
                      <dd className="is-mono">{selected.uri}</dd>
                    </div>
                    <div className="context-browser__fact">
                      <dt>范围</dt>
                      <dd>{scopeLabel(selected.uri)}</dd>
                    </div>
                    <div className="context-browser__fact">
                      <dt>格式</dt>
                      <dd>{formatLabel(selected.uri)}</dd>
                    </div>
                    <div className="context-browser__fact">
                      <dt>大小</dt>
                      <dd>{formatBytes(selected.size)}</dd>
                    </div>
                    <div className="context-browser__fact">
                      <dt>更新时间</dt>
                      <dd>{stamp ?? "—"}</dd>
                    </div>
                    <div className="context-browser__fact">
                      <dt>字符数</dt>
                      <dd>{charCount}</dd>
                    </div>
                    <div className="context-browser__fact">
                      <dt>行数</dt>
                      <dd>{lineCount}</dd>
                    </div>
                  </dl>
                  {markdown ? (
                    <section className="context-browser__outline" aria-label="内容大纲">
                      <h3>内容大纲</h3>
                      {outline.length > 0 ? (
                        <ul>
                          {outline.map((entry, index) => (
                            <li
                              key={`${entry.text}-${index}`}
                              style={{ paddingLeft: (entry.level - 1) * 14 }}
                            >
                              {entry.text}
                            </li>
                          ))}
                        </ul>
                      ) : (
                        <p>这个文件没有标题层级。</p>
                      )}
                    </section>
                  ) : null}
                  <p className="context-browser__detail-hint">
                    <Lock size={12} aria-hidden="true" />
                    上下文由会话与 OpenViking 维护；这里只做浏览。
                  </p>
                </div>
              ) : tab === "preview" ? (
                content ? (
                  markdown ? (
                    <div className="context-browser__preview">
                      <Markdown breaks>{content}</Markdown>
                    </div>
                  ) : (
                    <pre className="context-browser__plain">
                      {prettyContent(content, selected.uri)}
                    </pre>
                  )
                ) : (
                  <div className="context-browser__pane-state">
                    <p>这个上下文文件还是空的。</p>
                  </div>
                )
              ) : content ? (
                <SourceView content={content} markdown={markdown} />
              ) : (
                <div className="context-browser__pane-state">
                  <p>这个上下文文件还是空的。</p>
                </div>
              )}
            </>
          )}
        </div>

        <div className="context-browser__card-foot">
          <span>
            字符数 {content === null ? "—" : charCount}
            {contentTruncated ? " · 已截断" : ""}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="context-browser">
      <h1 className="sr-only">上下文管理</h1>

      <aside className="context-browser__sidebar" aria-label="上下文文件">
        <div className="context-browser__search">
          <div className="context-browser__field">
            <Search size={15} aria-hidden="true" />
            <input
              ref={searchRef}
              className="ui-input"
              type="text"
              role="searchbox"
              title="按 / 聚焦搜索"
              aria-keyshortcuts="/"
              value={search}
              placeholder="搜索上下文文件…"
              aria-label="搜索上下文文件"
              spellCheck={false}
              onChange={(event) => setSearch(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Escape" && search) {
                  event.preventDefault();
                  clearSearch();
                }
              }}
            />
            {search ? (
              <button
                type="button"
                className="context-browser__field-clear"
                aria-label="清除搜索"
                onClick={clearSearch}
              >
                <X size={12} aria-hidden="true" />
              </button>
            ) : null}
          </div>
          <IconButton
            label="刷新上下文目录"
            className="context-browser__icon-button context-browser__refresh"
            disabled={loading}
            onClick={refresh}
          >
            <RefreshCw
              size={15}
              className={loading ? "context-browser__spin" : undefined}
              aria-hidden="true"
            />
          </IconButton>
        </div>

        {treeError ? (
          <Alert tone="warning" className="context-browser__alert">
            <p>{treeError}</p>
            <Button variant="secondary" size="compact" onClick={refresh}>
              重试
            </Button>
          </Alert>
        ) : null}

        {tree?.truncated ? (
          <p className="context-browser__notice">
            目录较多，仅加载前 {tree.items.length} 项。
          </p>
        ) : null}

        <div className="context-browser__tree" aria-busy={loading || undefined}>
          {renderTree()}
        </div>
      </aside>

      <section className="context-browser__doc-pane" aria-label="上下文内容">
        {selected ? (
          <div className="context-browser__path-row">
            <p className="context-browser__path" title={selected.uri}>
              <span className="context-browser__path-dir">{dirPart}</span>
              <span className="context-browser__path-file">{filePart}</span>
            </p>
            <IconButton
              label={pathCopied ? "已复制路径" : "复制路径"}
              className="context-browser__icon-button"
              onClick={() => void copy("path", selected.uri)}
            >
              {pathCopied ? (
                <Check size={15} aria-hidden="true" />
              ) : (
                <Copy size={15} aria-hidden="true" />
              )}
            </IconButton>
          </div>
        ) : null}

        {selected ? (
          <div className="context-browser__meta-row">
            <span className="context-browser__chip">{scopeLabel(selected.uri)}</span>
            {stamp ? (
              <>
                <span className="context-browser__meta-sep" aria-hidden="true" />
                <span>更新于 {stamp}</span>
              </>
            ) : null}
            <span className="context-browser__readonly">
              <Lock size={11} aria-hidden="true" />
              只读
            </span>
          </div>
        ) : null}

        {renderDocument()}
      </section>

      <span className="sr-only" role="status">
        {copyState
          ? copyState.ok
            ? copyState.kind === "path"
              ? "已复制路径"
              : "已复制内容"
            : "复制失败"
          : ""}
      </span>
    </div>
  );
}
