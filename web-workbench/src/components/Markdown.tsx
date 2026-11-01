import { FileText } from "lucide-react";
import type { Root } from "mdast";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Plugin, PluggableList } from "unified";
import { remarkArtifactLinks } from "../markdown/remarkArtifactLinks";

const ARTIFACT_SCHEME = "artifact:";

interface MarkdownNode {
  type: string;
  value?: string;
  children?: MarkdownNode[];
}

/**
 * OpenViking writes plenty of files with single newlines instead of blank
 * lines; without this, those files render as one run-on paragraph. The plugin
 * turns soft line breaks into `<br>` while leaving block spacing untouched.
 */
const remarkSoftBreaks: Plugin<[], Root> = () => {
  return (tree) => {
    const walk = (node: MarkdownNode) => {
      if (!node.children) return;
      const children: MarkdownNode[] = [];
      for (const child of node.children) {
        if (
          child.type === "text" &&
          typeof child.value === "string" &&
          child.value.includes("\n")
        ) {
          child.value.split("\n").forEach((part, index) => {
            if (index > 0) children.push({ type: "break" });
            if (part) children.push({ type: "text", value: part });
          });
          continue;
        }
        walk(child);
        children.push(child);
      }
      node.children = children;
    };
    walk(tree as unknown as MarkdownNode);
  };
};

/**
 * Renders message bodies. react-markdown escapes raw HTML unless `rehype-raw`
 * is added, and it is not: message text comes out of a sandboxed Agent that can
 * be steered by whatever it reads, so it must not be able to inject markup.
 * Captured Agent output contains real GFM tables, hence the plugin.
 */
export function Markdown({
  children,
  artifactLinks = false,
  breaks = false,
}: {
  children: string;
  artifactLinks?: boolean;
  breaks?: boolean;
}) {
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={
          [
            remarkGfm,
            ...(breaks ? [remarkSoftBreaks] : []),
            ...(artifactLinks ? [remarkArtifactLinks] : []),
          ] satisfies PluggableList
        }
        // The default transform strips unknown schemes, which would gut the
        // in-app artifact links before they reach the `a` override.
        urlTransform={(url) =>
          url.startsWith(ARTIFACT_SCHEME) ? url : defaultUrlTransform(url)
        }
        components={{
          a: (props) =>
            typeof props.href === "string" &&
            props.href.startsWith(ARTIFACT_SCHEME) ? (
              <button
                type="button"
                className="artifact-chip"
                data-artifact-path={decodeURIComponent(
                  props.href.slice(ARTIFACT_SCHEME.length),
                )}
                title={decodeURIComponent(
                  props.href.slice(ARTIFACT_SCHEME.length),
                )}
              >
                <FileText size={13} aria-hidden="true" />
                <span>{props.children}</span>
              </button>
            ) : (
              <a {...props} target="_blank" rel="noopener noreferrer" />
            ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
