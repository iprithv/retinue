/** DOMPurify with a strict allowlist (§6.4). Links get noopener; external
 * images render as links (the strict CSP allows img-src 'self' data: only). */
import DOMPurify from "dompurify";

const ALLOWED_TAGS = [
  "p", "br", "hr", "blockquote", "pre", "code", "span", "em", "strong", "del", "s",
  "a", "img", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6",
  "table", "thead", "tbody", "tr", "th", "td", "sup", "sub", "input", "div",
];

const ALLOWED_ATTR = [
  "href", "src", "alt", "title", "class", "style", "start", "type", "checked",
  "disabled", "target", "rel", "colspan", "rowspan",
];

DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
  if (node.tagName === "INPUT") {
    // task-list checkboxes only, always inert
    node.setAttribute("disabled", "");
  }
});

export function sanitize(html: string): string {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR,
    ALLOW_DATA_ATTR: false,
  });
}
