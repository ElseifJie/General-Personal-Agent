import type { ContextNode, ContextTree } from "../gpaApi";

/** A directory tree built from OpenViking's flat `viking://` tree listing. */
export interface ContextTreeNode {
  node: ContextNode;
  description?: string;
  children: ContextTreeNode[];
}

const ROOT = "viking://";

const SCOPE_NOTES: Record<string, string> = {
  agent: "Agent 的能力、工具和经验",
  resources: "数据库内的共享知识资料",
};

/** Notes for the shared agent capability scope. */
const AGENT_NOTES: Record<string, string> = {
  endpoints: "共享的通信端点配置",
  payments: "共享的支付能力配置",
  skills: "账户级技能注册表（SKILL.md）",
  tools: "共享的工具配置",
};

/** Notes for the directories inside a user's personal scope. */
const USER_NOTES: Record<string, string> = {
  memories: "跨会话沉淀的长期记忆，按记忆类型分桶",
  peers: "按对端隔离的记忆（VeADK peers/<peer>/memories）",
  privacy: "密钥等敏感配置",
  resources: "用户的私有知识资料，按知识库索引分目录",
  sessions: "对话与任务运行记录",
  settings: "用户与记忆的运行配置",
  skills: "Agent 可用的技能与工具",
};

/** OpenViking 默认的 7 类记忆；与 settings/user_config.json 的 memory_policy 对齐。 */
const MEMORY_NOTES: Record<string, string> = {
  profile: "画像：稳定的自我描述",
  preferences: "偏好：口味、风格、习惯",
  entities: "实体：人、项目、组织",
  events: "事件：发生过的事实",
  cases: "案例：可复用的处理样例",
  experiences: "经验：做事方法与结论",
  trajectories: "轨迹：多步任务过程",
};

const ROOT_ORDER = ["user", "agent", "resources"];

function parentOf(uri: string): string {
  if (!uri.startsWith(ROOT)) return "";
  const rest = uri.slice(ROOT.length).replace(/\/+$/, "");
  const slash = rest.lastIndexOf("/");
  return slash < 0 ? ROOT : `${ROOT}${rest.slice(0, slash)}`;
}

function noteFor(path: string): string | undefined {
  const parts = path.split("/").filter(Boolean);
  if (parts.length === 1) return SCOPE_NOTES[parts[0]!];
  if (parts[0] === "agent") {
    return parts.length === 2 ? AGENT_NOTES[parts[1]!] : undefined;
  }
  if (parts[0] !== "user") return undefined;
  // user/<name>/...
  if (parts.length === 2) return "当前用户的上下文数据";
  if (parts.length === 3) return USER_NOTES[parts[2]!];
  if (parts[2] === "memories") return MEMORY_NOTES[parts[3]!];
  if (parts[2] === "peers") {
    if (parts.length === 4) return "该对端独立的记忆与会话空间";
    if (parts[4] === "memories") {
      return parts.length === 5 ? "按对端隔离的长期记忆，按类型分桶" : MEMORY_NOTES[parts[5]!];
    }
    return undefined;
  }
  if (parts[2] === "resources" && parts.length === 4) {
    return "知识库索引（如 uploads/、artifacts/）";
  }
  if (parts[2] === "sessions" && parts.length === 4) {
    return "单次会话：messages.jsonl 与 history/archive_NNN/";
  }
  if (parts[2] === "skills" && parts.length === 4) {
    return "Claude Skills 技能定义（SKILL.md）";
  }
  return undefined;
}

function compareNodes(left: ContextTreeNode, right: ContextTreeNode): number {
  if (left.node.isDir !== right.node.isDir) {
    return left.node.isDir ? 1 : -1;
  }
  return left.node.name.localeCompare(right.node.name, "zh-Hans-CN");
}

function sortTree(node: ContextTreeNode): void {
  node.children.sort(compareNodes);
  node.children.forEach(sortTree);
}

function rootRank(node: ContextTreeNode): number {
  const index = ROOT_ORDER.indexOf(node.node.name);
  return index < 0 ? ROOT_ORDER.length : index;
}

/**
 * Folds the flat listing into the scope forest. OpenViking may omit an
 * intermediate directory in a listing; those nodes are re-attached to the
 * closest existing ancestor so nothing silently disappears.
 */
export function buildContextTree(tree: ContextTree | null): ContextTreeNode[] {
  if (!tree) return [];
  const nodes = new Map<string, ContextTreeNode>();
  for (const item of tree.items) {
    if (!item.uri) continue;
    const description = noteFor(item.path);
    nodes.set(item.uri, {
      node: item,
      ...(description ? { description } : {}),
      children: [],
    });
  }

  const roots: ContextTreeNode[] = [];
  for (const node of nodes.values()) {
    let parentUri = parentOf(node.node.uri);
    let parent = nodes.get(parentUri);
    while (!parent && parentUri && parentUri !== ROOT) {
      parentUri = parentOf(parentUri);
      parent = nodes.get(parentUri);
    }
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  roots.sort((left, right) => rootRank(left) - rootRank(right) || compareNodes(left, right));
  roots.forEach(sortTree);
  return roots;
}

/**
 * The file the page opens on load: a `memories/profile/` document (OV's stable
 * self-description bucket), else `memories/identity.md`, else any memory file.
 */
export function defaultSelection(tree: ContextTree | null): string | null {
  if (!tree?.userUri) return null;
  const prefix = `${tree.userUri}/memories/`;
  const files = tree.items.filter(
    (item) => !item.isDir && item.uri.startsWith(prefix),
  );
  return (
    files.find((item) => item.uri.startsWith(`${prefix}profile/`)) ??
    files.find((item) => item.uri === `${prefix}identity.md`) ??
    files[0]
  )?.uri ?? null;
}

export function isFileVisible(tree: ContextTree | null, uri: string): boolean {
  return Boolean(tree?.items.some((item) => !item.isDir && item.uri === uri));
}

function pathTo(nodes: ContextTreeNode[], uri: string): string[] | null {
  for (const node of nodes) {
    if (node.node.uri === uri) return [];
    if (!node.node.isDir) continue;
    const nested = pathTo(node.children, uri);
    if (nested) return [node.node.uri, ...nested];
  }
  return null;
}

export function ancestorUris(nodes: ContextTreeNode[], uri: string | null): string[] {
  if (!uri) return [];
  return pathTo(nodes, uri) ?? [];
}

/**
 * Root scopes and the signed-in user's own scope start expanded; everything
 * else follows the selection. Deep session folders stay collapsed.
 */
export function defaultCollapsed(
  nodes: ContextTreeNode[],
  selectedUri: string | null,
  userUri: string,
): Set<string> {
  const keep = new Set<string>();
  for (const node of nodes) {
    if (node.node.isDir) keep.add(node.node.uri);
  }
  if (userUri) keep.add(userUri);
  for (const uri of ancestorUris(nodes, selectedUri)) keep.add(uri);

  const collapsed = new Set<string>();
  const visit = (node: ContextTreeNode) => {
    if (!node.node.isDir) return;
    if (!keep.has(node.node.uri)) collapsed.add(node.node.uri);
    node.children.forEach(visit);
  };
  nodes.forEach(visit);
  return collapsed;
}

export function searchFiles(
  nodes: ContextTreeNode[],
  query: string,
  limit = 80,
): ContextNode[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [];
  const hits: ContextNode[] = [];
  const visit = (node: ContextTreeNode) => {
    if (node.node.isDir) {
      node.children.forEach(visit);
      return;
    }
    if (hits.length >= limit) return;
    if (
      node.node.name.toLowerCase().includes(needle) ||
      node.node.path.toLowerCase().includes(needle)
    ) {
      hits.push(node.node);
    }
  };
  nodes.forEach(visit);
  return hits;
}

export function formatStamp(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatBytes(size: number | null | undefined): string {
  if (size == null) return "—";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/** Which OpenViking scope a path belongs to; shown as the detail chip. */
export function scopeLabel(uri: string): string {
  if (uri.startsWith(`${ROOT}agent`)) return "Agent";
  if (uri.startsWith(`${ROOT}resources`)) return "共享";
  if (uri.startsWith(`${ROOT}user`)) return "用户";
  return "上下文";
}

export function isMarkdown(uri: string): boolean {
  return /\.md$/i.test(uri);
}

export function formatLabel(uri: string): string {
  const match = /\.([a-z0-9]+)$/i.exec(uri);
  switch (match?.[1]?.toLowerCase()) {
    case "md":
      return "Markdown";
    case "json":
      return "JSON";
    case "jsonl":
      return "JSONL";
    case "markdown":
      return "Markdown";
    case "txt":
      return "文本";
    default:
      return "文本";
  }
}

/**
 * Pretty-prints structured files for the preview tab. Anything that is not
 * valid JSON falls back to the raw text so the view never loses content.
 */
export function prettyContent(content: string, uri: string): string {
  if (/\.json$/i.test(uri)) {
    try {
      return JSON.stringify(JSON.parse(content), null, 2);
    } catch {
      return content;
    }
  }
  if (/\.jsonl$/i.test(uri)) {
    return content
      .split("\n")
      .filter((line) => line.trim())
      .map((line) => {
        try {
          return JSON.stringify(JSON.parse(line));
        } catch {
          return line;
        }
      })
      .join("\n");
  }
  return content;
}
