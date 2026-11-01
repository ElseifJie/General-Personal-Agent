import type { Plugin } from "unified";

interface MdNode {
  type: string;
  value?: string;
  url?: string;
  data?: Record<string, unknown>;
  children?: MdNode[];
}

const OUTPUT_PREFIX = "/mnt/session/outputs/";
const PATH_PATTERN = /\/mnt\/session\/outputs\/[^\s`*~[\]()<>|"'#\\]+/g;
const PATH_IN_TEXT = new RegExp(`^${PATH_PATTERN.source}$`);
const TRAILING_PUNCTUATION = /[.,;:!?…、。，；：！？)】》"'”’]+$/;

/**
 * Agents wrap display-worthy outputs in `<display>` tags. The renderer escapes
 * raw HTML, so the tags would surface as literal text around the artifact
 * chip; they are Agent chrome, not content, and are removed on sight — both as
 * the `html` nodes micromark produces and as text, in case the source arrives
 * pre-escaped.
 */
const DISPLAY_TAG = /<\/?display(?:\s[^>]*)?>/g;
const DISPLAY_TAG_NODE = /^<\/?display(?:\s[^>]*)?>$/;

function isDisplayTagNode(node: MdNode): boolean {
  return node.type === "html" && DISPLAY_TAG_NODE.test(node.value ?? "");
}

function withoutDisplayTags(value: string): string {
  return value.replace(DISPLAY_TAG, "");
}

function basenameOf(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

function artifactLinkNode(mountPath: string): MdNode {
  return {
    type: "link",
    url: `artifact:${encodeURIComponent(mountPath)}`,
    children: [{ type: "text", value: basenameOf(mountPath) }],
  };
}

/**
 * Rewrites `/mnt/session/outputs/<name>` occurrences into in-app artifact
 * links. Runs on the parsed AST so fenced code, tables and links stay
 * structurally intact; inline code converts only when the whole span is the
 * path, which is how Agents habitually mention one.
 */
export const remarkArtifactLinks: Plugin<[], MdNode> = () => {
  return (tree) => {
    transformChildren(tree);
  };
};

function transformChildren(node: MdNode) {
  if (!node.children) return;
  const next: MdNode[] = [];
  for (const child of node.children) {
    if (isDisplayTagNode(child)) continue;
    if (child.type === "text" && child.value) {
      const value = withoutDisplayTags(child.value);
      if (value.length > 0) next.push(...splitTextNode(value));
    } else if (
      child.type === "inlineCode" &&
      child.value?.startsWith(OUTPUT_PREFIX)
    ) {
      next.push(
        PATH_IN_TEXT.test(child.value) ? artifactLinkNode(child.value) : child,
      );
    } else {
      transformChildren(child);
      next.push(child);
    }
  }
  node.children = next;
}

function splitTextNode(value: string): MdNode[] {
  const nodes: MdNode[] = [];
  let cursor = 0;
  PATH_PATTERN.lastIndex = 0;
  for (
    let match = PATH_PATTERN.exec(value);
    match !== null;
    match = PATH_PATTERN.exec(value)
  ) {
    const raw = match[0];
    const mountPath = raw.replace(TRAILING_PUNCTUATION, "");
    if (mountPath.length === 0) continue;
    const start = match.index;
    const end = start + mountPath.length;
    if (start > cursor) {
      nodes.push({ type: "text", value: value.slice(cursor, start) });
    }
    nodes.push(artifactLinkNode(mountPath));
    cursor = end;
  }
  if (nodes.length === 0) return [{ type: "text", value }];
  if (cursor < value.length) {
    nodes.push({ type: "text", value: value.slice(cursor) });
  }
  return nodes;
}
