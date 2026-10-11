import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, access, rm, chmod, readdir, stat, utimes } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { CompanionRequired, digest, distribution, installRuntime, validateManifest } from "../src/runtime";

const manifest = { version: "0.2.0", sha256: "0".repeat(64), platform: "darwin-arm64" as const, unpacked_bytes: 100 };
test("rejects malformed runtime manifests", () => {
  assert.throws(() => validateManifest({ ...manifest, version: "../../models" }));
  assert.throws(() => validateManifest({ ...manifest, sha256: "invalid" }));
});
test("Store mode never falls back to installing an executable archive", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic-search-companion-"));
  try {
    const assets = join(root, "assets"), storage = join(root, "storage");
    await mkdir(assets); await mkdir(join(storage, "data"), { recursive: true });
    await writeFile(join(storage, "data/index.sqlite3"), "preserve");
    await writeFile(join(assets, "distribution.json"), JSON.stringify({ mode: "companion", installer_url: "https://github.com/nikusti/semantic-search/releases/tag/v0.2.0" }));
    // Even if an archive is present, a Store build must not execute it.
    await writeFile(join(assets, "runtime.zip"), "not executable");
    await assert.rejects(installRuntime({ assets, storage }, manifest), CompanionRequired);
    assert.equal(await readFile(join(storage, "data/index.sqlite3"), "utf8"), "preserve");
    await assert.rejects(access(join(storage, "runtime/install.lock")));
    await writeFile(join(assets, "distribution.json"), JSON.stringify({ mode: "companion", installer_url: "https://example.com/engine" }));
    await assert.rejects(distribution(assets), /installer link/);
    await writeFile(join(assets, "distribution.json"), JSON.stringify({ mode: "download" }));
    await assert.rejects(distribution(assets), /package/);
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("corrupt archive leaves models and indexes untouched and releases the lock", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic-search-test-"));
  try {
    const assets = join(root, "assets"), storage = join(root, "storage");
    await mkdir(assets); await mkdir(join(storage, "data"), { recursive: true });
    await mkdir(join(storage, "models"));
    await writeFile(join(storage, "data", "index.sqlite3"), "preserve");
    await writeFile(join(storage, "models", "weight"), "preserve");
    await writeFile(join(assets, "runtime.zip"), "corrupt");
    assert.notEqual(await digest(join(assets, "runtime.zip")), manifest.sha256);
    await assert.rejects(installRuntime({ assets, storage }, manifest), /integrity/);
    assert.equal(await readFile(join(storage, "data", "index.sqlite3"), "utf8"), "preserve");
    assert.equal(await readFile(join(storage, "models", "weight"), "utf8"), "preserve");
    await assert.rejects(access(join(storage, "runtime", "install.lock")));
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("an installed runtime is reused without unpacking or needing the archive", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-search-test-"));
  try {
    const runtime = join(storage, "runtime", manifest.version);
    await mkdir(join(runtime, "python/bin"), { recursive: true });
    await writeFile(join(runtime, "python/bin/python3.12"), "#!/bin/sh\nexit 0\n");
    await chmod(join(runtime, "python/bin/python3.12"), 0o755);
    await writeFile(join(runtime, "installed.json"), JSON.stringify(manifest));
    await writeFile(join(runtime, ".startup-cache-ready"), "1");
    assert.equal(await installRuntime({ assets: "/unneeded", storage }, manifest), runtime);
  } finally { await rm(storage, { recursive: true, force: true }); }
});

test("an installed runtime prepares its local startup cache once and preserves user data", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-search-cache-test-"));
  try {
    const runtime = join(storage, "runtime", manifest.version);
    const executable = join(runtime, "python/bin/python3.12");
    const log = join(runtime, "compile.log");
    await mkdir(join(runtime, "python/bin"), { recursive: true });
    await mkdir(join(storage, "data")); await mkdir(join(storage, "models"));
    await writeFile(join(storage, "data/index.sqlite3"), "preserve");
    await writeFile(join(storage, "models/weights"), "preserve");
    await writeFile(join(runtime, "installed.json"), JSON.stringify(manifest));
    await writeFile(executable, `#!${process.execPath}\nrequire('node:fs').appendFileSync(${JSON.stringify(log)},JSON.stringify(process.argv.slice(2))+'\\n');\n`);
    await chmod(executable, 0o755);
    for (let i = 0; i < 2; i++) assert.equal(await installRuntime({ assets: "/unneeded", storage }, manifest), runtime);
    const calls = (await readFile(log, "utf8")).trim().split("\n").map(line => JSON.parse(line) as string[]);
    assert.equal(calls.filter(args => args.includes("compileall")).length, 1);
    assert.equal(calls.filter(args => args.includes("tinysearch.coreml_assets")).length, 2);
    assert.equal(await readFile(join(runtime, ".startup-cache-ready"), "utf8"), "1");
    assert.equal(await readFile(join(storage, "data/index.sqlite3"), "utf8"), "preserve");
    assert.equal(await readFile(join(storage, "models/weights"), "utf8"), "preserve");
  } finally { await rm(storage, { recursive: true, force: true }); }
});


import { addExclusionFolders, reconcileValues } from "../src/settings-values";

test("another launcher refreshes untouched settings without discarding local edits", () => {
  const initial = { roots: "~/Documents", excluded_paths: "~/Documents/private", dimensions: "512" };
  const external = { ...initial, excluded_paths: "~/Documents/private\n~/Documents/another-folder" };
  assert.deepEqual(reconcileValues(initial, initial, external), external);
  const draft = { ...initial, dimensions: "256" };
  assert.deepEqual(reconcileValues(draft, initial, external), { ...external, dimensions: "256" });
  const exclusionDraft = { ...initial, excluded_paths: "~/Documents/local-draft" };
  assert.equal(reconcileValues(exclusionDraft, initial, external).excluded_paths, exclusionDraft.excluded_paths);
});

test("a native picker selection adds exclusions without replacing earlier folders", () => {
  const saved = "~/Documents/private\n~/Pictures/private";
  assert.equal(addExclusionFolders(saved, ["~/Downloads/private"]), saved + "\n~/Downloads/private");
  assert.equal(addExclusionFolders(saved, ["~/Documents/private"]), saved);
  assert.equal(addExclusionFolders(saved, []), saved);
});

import { fileURLToPath } from "node:url";
import { createPreviewFiles } from "../src/preview-files";

test("preview files are private, bounded, validated and removed on close", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic search preview-"));
  const image = "data:image/jpeg;base64," + Buffer.from([0xff, 0xd8, 0xff, 0xd9]).toString("base64");
  try {
    const cache = await createPreviewFiles(root);
    const reference = await cache.write(image);
    const path = fileURLToPath(reference);
    assert.ok(path.startsWith(join(root, "preview-cache/session-")));
    assert.equal((await stat(path)).mode & 0o777, 0o600);
    assert.deepEqual(await readFile(path), Buffer.from([0xff, 0xd8, 0xff, 0xd9]));
    await assert.rejects(cache.write("file:///private/data.jpg"), /Invalid/);
    await assert.rejects(cache.write("data:image/jpeg;base64,YmFk"), /JPEG/);
    await assert.rejects(cache.write("data:image/jpeg;base64," + "A".repeat(8 * 1024 * 1024 + 1)), /Invalid/);
    await cache.close();
    await assert.rejects(access(path));
    await assert.rejects(cache.write(image), /closed/);
    const stale = join(root, "preview-cache/session-expired");
    const unrelated = join(root, "preview-cache/preserve");
    await mkdir(stale); await mkdir(unrelated);
    await utimes(stale, new Date(0), new Date(0));
    const next = await createPreviewFiles(root);
    await assert.rejects(access(stale));
    await access(unrelated);
    await next.close();
    assert.deepEqual(await readdir(join(root, "preview-cache")), ["preserve"]);
  } finally { await rm(root, { recursive: true, force: true }); }
});

test("Tinycast keeps its supported inline preview format without writing a cache", async () => {
 const support = "/unneeded/com.tinycast.app/extension-support/example";
 const cache = await createPreviewFiles(support);
 const image = "data:image/jpeg;base64,/9j/2Q==";
 assert.equal(await cache.write(image), image);
 await cache.close();
 await assert.rejects(access(support));
});
