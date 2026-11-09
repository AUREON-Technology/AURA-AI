// ESLint for the operator console (audit G-10): `npm run lint` was `next lint` with no config, an
// interactive prompt rather than a gate. Next's own rule sets (`next/core-web-vitals`,
// `next/typescript`), loaded through FlatCompat because eslint-config-next 15 ships them in the
// legacy shape, plus the generated and report directories excluded.
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { FlatCompat } from "@eslint/eslintrc";

const compat = new FlatCompat({ baseDirectory: dirname(fileURLToPath(import.meta.url)) });

const config = [
  { ignores: [".next/**", "node_modules/**", "playwright-report/**", "test-results/**", "next-env.d.ts"] },
  ...compat.extends("next/core-web-vitals", "next/typescript"),
];

export default config;
