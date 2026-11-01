import { useMemo } from "react";

interface InlineToken {
  kind: "text" | "mark" | "strong" | "em" | "code" | "link";
  text: string;
}

const INLINE_PATTERN =
  /(\*\*[^*\n]+\*\*|__[^_\n]+__|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)|_[^_\n]+_|\*[^*\n]+\*)/g;

function inlineTokens(text: string): InlineToken[] {
  const tokens: InlineToken[] = [];
  let cursor = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      tokens.push({ kind: "text", text: text.slice(cursor, index) });
    }
    const value = match[0];
    if (value.startsWith("**") || value.startsWith("__")) {
      const mark = value.slice(0, 2);
      tokens.push({ kind: "mark", text: mark });
      tokens.push({ kind: "strong", text: value.slice(2, -2) });
      tokens.push({ kind: "mark", text: mark });
    } else if (value.startsWith("`")) {
      tokens.push({ kind: "mark", text: "`" });
      tokens.push({ kind: "code", text: value.slice(1, -1) });
      tokens.push({ kind: "mark", text: "`" });
    } else if (value.startsWith("[")) {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value);
      if (link) {
        tokens.push({ kind: "mark", text: "[" });
        tokens.push({ kind: "link", text: link[1] ?? value });
        tokens.push({ kind: "mark", text: `](${link[2]})` });
      } else {
        tokens.push({ kind: "text", text: value });
      }
    } else {
      const mark = value.startsWith("_") ? "_" : "*";
      tokens.push({ kind: "mark", text: mark });
      tokens.push({ kind: "em", text: value.slice(1, -1) });
      tokens.push({ kind: "mark", text: mark });
    }
    cursor = index + value.length;
  }
  if (cursor < text.length) {
    tokens.push({ kind: "text", text: text.slice(cursor) });
  }
  return tokens;
}

function InlineSource({ text }: { text: string }) {
  const tokens = useMemo(() => inlineTokens(text), [text]);
  return (
    <>
      {tokens.map((token, index) => (
        <span className={`context-browser__md-${token.kind}`} key={index}>
          {token.text}
        </span>
      ))}
    </>
  );
}

function SourceLine({ line }: { line: string }) {
  const heading = /^(#{1,6})\s+(.*)$/.exec(line);
  if (heading) {
    return (
      <>
        <span className="context-browser__md-mark">{heading[1]}</span>
        {" "}
        <span className="context-browser__md-heading">{heading[2]}</span>
      </>
    );
  }
  if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
    return <span className="context-browser__md-mark">{line}</span>;
  }
  const bullet = /^(\s*)([-*+])(\s+)(.*)$/.exec(line);
  if (bullet) {
    return (
      <>
        {bullet[1]}
        <span className="context-browser__md-mark">{bullet[2]}</span>
        {bullet[3]}
        <InlineSource text={bullet[4] ?? ""} />
      </>
    );
  }
  const ordered = /^(\s*)(\d+\.)(\s+)(.*)$/.exec(line);
  if (ordered) {
    return (
      <>
        {ordered[1]}
        <span className="context-browser__md-mark">{ordered[2]}</span>
        {ordered[3]}
        <InlineSource text={ordered[4] ?? ""} />
      </>
    );
  }
  const quote = /^(\s*>\s?)(.*)$/.exec(line);
  if (quote) {
    return (
      <>
        <span className="context-browser__md-mark">{quote[1]}</span>
        <span className="context-browser__md-quote">{quote[2]}</span>
      </>
    );
  }
  return <InlineSource text={line} />;
}

export function SourceView({
  content,
  markdown = true,
}: {
  content: string;
  markdown?: boolean;
}) {
  const lines = useMemo(() => content.split("\n"), [content]);
  let fenced = false;
  return (
    <div className="context-browser__code" role="region" aria-label="上下文源码">
      {lines.map((line, index) => {
        const isFence = markdown && /^\s*(`{3,}|~{3,})/.test(line);
        if (isFence) fenced = !fenced;
        const body =
          line === "" ? (
            "\u00a0"
          ) : !markdown ? (
            line
          ) : isFence || fenced ? (
            <span className="context-browser__md-fence">{line}</span>
          ) : (
            <SourceLine line={line} />
          );
        return (
          <div className="context-browser__code-line" key={index}>
            <span className="context-browser__code-no" aria-hidden="true">
              {index + 1}
            </span>
            <code className="context-browser__code-text">{body}</code>
          </div>
        );
      })}
    </div>
  );
}
