import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";
import noDirectAdapterImport from "./eslint-rules/no-direct-adapter-import.cjs";

const compat = new FlatCompat({
  baseDirectory: dirname(fileURLToPath(import.meta.url)),
});

const uiFiles = ["app/**/*.{ts,tsx}", "components/**/*.{ts,tsx}", "middleware.ts"];

const config = [
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
  {
    files: uiFiles,
    plugins: {
      atlasgo: { rules: { "no-direct-adapter-import": noDirectAdapterImport } },
    },
    rules: {
      "atlasgo/no-direct-adapter-import": "error",
    },
  },
];

export default config;
