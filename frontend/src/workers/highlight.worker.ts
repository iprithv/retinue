/// <reference lib="webworker" />
/** Shiki syntax highlighting off the main thread (§6.4, D21). Fine-grained
 * bundle: an enumerated set of common grammars (statically imported so the
 * build emits only these), JS regex engine (no wasm chunk). Everything loads
 * lazily inside this worker on first use. */

import type { HighlighterCore } from "shiki/core";

const LANG_ALIASES: Record<string, string> = {
  js: "javascript",
  ts: "typescript",
  py: "python",
  rb: "ruby",
  rs: "rust",
  sh: "shellscript",
  bash: "shellscript",
  zsh: "shellscript",
  shell: "shellscript",
  yml: "yaml",
  "c++": "cpp",
  "c#": "csharp",
  cs: "csharp",
  golang: "go",
  dockerfile: "docker",
  md: "markdown",
};

let highlighterPromise: Promise<HighlighterCore> | null = null;

async function getHighlighter(): Promise<HighlighterCore> {
  highlighterPromise ??= (async () => {
    const [{ createHighlighterCore }, { createJavaScriptRegexEngine }] = await Promise.all([
      import("shiki/core"),
      import("shiki/engine/javascript"),
    ]);
    return createHighlighterCore({
      themes: [import("shiki/themes/github-light.mjs"), import("shiki/themes/github-dark.mjs")],
      langs: [
        import("shiki/langs/javascript.mjs"),
        import("shiki/langs/typescript.mjs"),
        import("shiki/langs/jsx.mjs"),
        import("shiki/langs/tsx.mjs"),
        import("shiki/langs/python.mjs"),
        import("shiki/langs/rust.mjs"),
        import("shiki/langs/go.mjs"),
        import("shiki/langs/java.mjs"),
        import("shiki/langs/c.mjs"),
        import("shiki/langs/cpp.mjs"),
        import("shiki/langs/csharp.mjs"),
        import("shiki/langs/ruby.mjs"),
        import("shiki/langs/php.mjs"),
        import("shiki/langs/kotlin.mjs"),
        import("shiki/langs/swift.mjs"),
        import("shiki/langs/json.mjs"),
        import("shiki/langs/yaml.mjs"),
        import("shiki/langs/toml.mjs"),
        import("shiki/langs/shellscript.mjs"),
        import("shiki/langs/sql.mjs"),
        import("shiki/langs/html.mjs"),
        import("shiki/langs/css.mjs"),
        import("shiki/langs/markdown.mjs"),
        import("shiki/langs/diff.mjs"),
        import("shiki/langs/docker.mjs"),
      ],
      engine: createJavaScriptRegexEngine({ forgiving: true }),
    });
  })();
  return highlighterPromise;
}

self.onmessage = async (
  event: MessageEvent<{ id: number; code: string; language: string }>,
) => {
  const { id, code, language } = event.data;
  try {
    const highlighter = await getHighlighter();
    const lang = LANG_ALIASES[language.toLowerCase()] ?? language.toLowerCase();
    if (!highlighter.getLoadedLanguages().includes(lang)) {
      self.postMessage({ id, html: null });
      return;
    }
    const html = highlighter.codeToHtml(code, {
      lang,
      themes: { light: "github-light", dark: "github-dark" },
      defaultColor: "light-dark()",
    });
    self.postMessage({ id, html });
  } catch {
    self.postMessage({ id, html: null });
  }
};
