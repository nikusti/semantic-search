import { build } from "esbuild";
import { cp, mkdir } from "node:fs/promises";
await mkdir("dist", { recursive: true });
await build({
  entryPoints: ["src/semantic-search.tsx", "src/model-settings.tsx"], outdir: "dist", bundle: true,
  platform: "node", format: "cjs", target: "es2022", jsx: "automatic",
  external: ["react", "react/jsx-runtime", "@raycast/api"],
});
await cp("package.json", "dist/package.json");
await cp("assets", "dist/assets", { recursive: true });
