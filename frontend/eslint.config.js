// Flat config: tsc (strict) owns type errors; eslint owns correctness lints
// that types can't see. Style is left to the formatter, not argued here.
import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "src/lib/api/openapi.json"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["src/**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // the streaming store and SSE layer legitimately deal in unknown JSON
      "@typescript-eslint/no-explicit-any": "off",
    },
  },
);
