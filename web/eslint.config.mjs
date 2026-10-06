import next from "eslint-config-next";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

/**
 * ESLint config.
 *
 * `eslint-config-next` v16 exports native flat config, so it is spread directly
 * -- no `FlatCompat` bridge. The bridge was tried first and is wrong here: it
 * re-normalises an already-flat config through the eslintrc schema and dies on
 * a circular plugin reference.
 *
 * `core-web-vitals` catches the React mistakes that survive a type check (bad
 * effect dependencies, invalid nesting, unescaped text); `typescript` adds the
 * type-aware rules. Together they are what makes `eslint` worth running
 * alongside `tsc --noEmit` rather than as a restatement of it.
 *
 * Two exclusions, each for a reason that would not survive being flipped back:
 *
 * - `src/lib/api/types.ts` is generated. Linting generated output means either
 *   editing the generator or muting rules against it, and a hand-applied
 *   suppression in a generated file is silently discarded by the next
 *   regeneration -- so the rule would apply to nobody and appear to work.
 * - `scripts/` is build-time tooling running under Node, where top-level await
 *   and console output are the interface rather than lint failures.
 */
const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  ...next,
  {
    ignores: [
      ".next/**",
      "node_modules/**",
      "scripts/**",
      "src/lib/api/types.ts",
    ],
  },
  {
    // Config files name a binding and export it for the tool's own sake --
    // `postcss.config.mjs` exporting `config` and this file exporting
    // `eslintConfig`. `no-unused-vars` cannot see that the export is the point,
    // and turning it off repo-wide to accommodate two config files would cost
    // the rule where it does real work. Scoped to the configs instead.
    files: ["*.config.mjs", "*.config.js"],
    rules: { "@typescript-eslint/no-unused-vars": "off" },
  },
];

export default eslintConfig;