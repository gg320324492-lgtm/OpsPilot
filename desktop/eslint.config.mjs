/**
 * ESLint for the Electron shell.
 *
 * ## Why this is not `eslint-config-next`
 *
 * That config exists to police a Next.js *application*: Server Components,
 * client boundaries, React hooks. None of that is what `desktop/` contains. The
 * shell is a Node main process, a sandboxed preload, and a browser page that
 * touches no framework — and the mistakes worth catching here are different
 * ones. An unawaited promise in a lifecycle path is the big one: `void
 * startApplication(...)` that silently stopped working means the containers
 * never come down, and nothing else in a type check notices.
 *
 * `web/eslint.config.mjs` argues at length for using native flat config rather
 * than a bridge; this file does the same thing.
 *
 * ## Two blocks, because half this package is not TypeScript
 *
 * The `.ts` block is type-aware (`recommendedTypeChecked` with a project
 * service), which is what makes `no-floating-promises` and `no-misused-promises`
 * real rather than aspirational. The renderer and the build scripts are plain
 * `.js`/`.mjs` and are deliberately *not* in `tsconfig.json` — they have no type
 * information, so the type-aware rules cannot run on them at all and are
 * switched off there rather than erroring.
 *
 * Two rules are relaxed for a stated reason rather than by habit:
 *
 *   * `no-console` off — the main process's console output *is* the log a
 *     developer reads when a desktop app misbehaves, and there is nowhere else
 *     for it to go. `web/eslint.config.mjs` excludes `scripts/` for the same
 *     reason in the same words.
 *   * `no-unused-vars` keeps `argsIgnorePattern: "^_"` rather than being
 *     switched off, so an unused local is still an error.
 */

import js from "@eslint/js";
import tseslint from "typescript-eslint";

/** The browser globals the renderer and the shell's own code touch. */
const BROWSER_GLOBALS = {
  window: "readonly",
  document: "readonly",
  console: "readonly",
  URL: "readonly",
  Response: "readonly",
  setTimeout: "readonly",
  clearTimeout: "readonly",
  process: "readonly",
};

const eslintConfig = tseslint.config(
  {
    ignores: ["dist/**", "node_modules/**", "test-results/**", "playwright-report/**"],
  },
  js.configs.recommended,
  {
    files: ["**/*.ts"],
    extends: [tseslint.configs.recommendedTypeChecked],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "@typescript-eslint/no-floating-promises": "error",
      "@typescript-eslint/no-misused-promises": "error",
      "@typescript-eslint/require-await": "error",
      eqeqeq: ["error", "always"],
      "no-console": "off",
    },
  },
  {
    files: ["**/*.js", "**/*.mjs"],
    extends: [tseslint.configs.recommended, tseslint.configs.disableTypeChecked],
    languageOptions: { globals: BROWSER_GLOBALS },
    rules: {
      "no-console": "off",
    },
  },
  {
    // The test doubles in `e2e/` imitate asynchronous ports synchronously — a
    // `portBusy` that resolves immediately still has to return a promise, and
    // that is the whole point of it. `require-await` would flag every one of
    // them, and answering it by adding a meaningless `await` would be worse.
    files: ["e2e/**/*.ts"],
    rules: {
      "@typescript-eslint/require-await": "off",
    },
  },
);

export default eslintConfig;