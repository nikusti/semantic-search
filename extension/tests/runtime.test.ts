import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, readFile, writeFile, access, rm, chmod } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { digest, installRuntime, validateManifest } from "../src/runtime";

const manifest = { version: "0.2.0", sha256: "0".repeat(64), platform: "darwin-arm64" as const, unpacked_bytes: 100 };
test("rejects malformed runtime manifests", () => {
  assert.throws(() => validateManifest({ ...manifest, version: "../../models" }));
  assert.throws(() => validateManifest({ ...manifest, sha256: "invalid" }));
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
    await writeFile(join(runtime, "python/bin/python3.12"), "existing");
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
    assert.equal((await readFile(log, "utf8")).trim().split("\n").length, 1);
    assert.ok(JSON.parse((await readFile(log, "utf8")).trim()).includes("compileall"));
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
