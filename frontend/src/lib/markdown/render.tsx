/** Incremental markdown renderer (§6.4): stable blocks parse once and are
 * memoized by content hash — they never re-render while the tail streams.
 * Code blocks paint instantly as plain <pre> and upgrade in place when the
 * Shiki worker returns highlighted HTML. */
import MarkdownIt from "markdown-it";
import { memo, useEffect, useMemo, useRef } from "react";
import { contentHash, splitBlocks } from "./blocks";
import { requestHighlight } from "./highlight";
import { sanitize } from "./sanitize";

const md = new MarkdownIt({ html: false, linkify: true, breaks: false });

// External images become links: the strict CSP (img-src 'self' data:) would
// block them anyway; data: URIs still render inline.
md.renderer.rules.image = (tokens, idx, options, _env, self) => {
  const token = tokens[idx]!;
  const src = token.attrGet("src") ?? "";
  if (/^data:image\//i.test(src)) return self.renderToken(tokens, idx, options);
  const label = token.content || src;
  return `<a href="${md.utils.escapeHtml(src)}">🖼 ${md.utils.escapeHtml(label)}</a>`;
};

export function renderMarkdown(source: string): string {
  return sanitize(md.render(source));
}

function useShikiUpgrade(containerRef: React.RefObject<HTMLDivElement | null>, html: string) {
  useEffect(() => {
    const container = containerRef.current;
    if (!container || !html.includes("<pre")) return;
    let cancelled = false;
    for (const pre of container.querySelectorAll("pre > code")) {
      const language = /language-([\w+-]+)/.exec(pre.className)?.[1] ?? "text";
      const code = pre.textContent ?? "";
      if (!code.trim()) continue;
      void requestHighlight(code, language).then((highlighted) => {
        if (cancelled || !highlighted) return;
        const wrapper = document.createElement("div");
        wrapper.innerHTML = sanitize(highlighted);
        const replacement = wrapper.firstElementChild;
        pre.parentElement?.replaceWith(replacement ?? pre.parentElement);
      });
    }
    return () => {
      cancelled = true;
    };
  }, [containerRef, html]);
}

const StableBlock = memo(function StableBlock({ source }: { source: string }) {
  const html = useMemo(() => renderMarkdown(source), [source]);
  const ref = useRef<HTMLDivElement | null>(null);
  useShikiUpgrade(ref, html);
  // sanitized upstream by DOMPurify (§6.4) — the only innerHTML sink in the app
  return <div ref={ref} className="md" dangerouslySetInnerHTML={{ __html: html }} />;
});

export function Markdown({ text }: { text: string }) {
  const { stable, tail } = splitBlocks(text);
  const tailHtml = tail ? renderMarkdown(tail) : "";
  return (
    <>
      {stable.map((block) => (
        <StableBlock key={contentHash(block)} source={block} />
      ))}
      {tailHtml ? (
        <div className="md" dangerouslySetInnerHTML={{ __html: tailHtml }} />
      ) : null}
    </>
  );
}
