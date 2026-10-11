/** Shared, offline installation and startup of the bundled local service. */
import { access, mkdir, readFile, rename, rm, stat, writeFile } from "node:fs/promises";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { join } from "node:path";
import { arch, platform, release } from "node:os";

const execute = promisify(execFile);
export type RuntimeManifest = { version: string; sha256: string; platform: "darwin-arm64"; unpacked_bytes: number };
type Options = { assets: string; storage: string; port?: number; progress?: (message: string) => void };
const delay = (ms: number) => new Promise(resolve => setTimeout(resolve, ms));
export async function digest(path: string) {
  // Use the macOS checksum tool: Tinycast does not expose live Node streams.
  const { stdout } = await execute("/usr/bin/shasum", ["-a", "256", path], { timeout: 30_000 });
  const value = stdout.trim().split(/\s+/)[0];
  if (!/^[a-f0-9]{64}$/.test(value)) throw new Error("Could not verify the bundled engine archive.");
  return value;
}
export function validateManifest(value: RuntimeManifest) {
  if (!/^\d+\.\d+\.\d+$/.test(value.version) || !/^[a-f0-9]{64}$/.test(value.sha256) || value.platform !== "darwin-arm64" || !(value.unpacked_bytes > 0)) {
    throw new Error("The bundled search engine is incomplete. Download the installation ZIP again.");
  }
  return value;
}
async function info(base: string) {
  try {
    const response = await fetch(`${base}/v1/info`, { signal: AbortSignal.timeout(1500) });
    if (!response.ok) return undefined;
    const value = await response.json() as { application: string; version: string; protocol: number };
    return value.application === "semantic-search" && value.protocol === 1 ? value : undefined;
  } catch { return undefined; }
}
async function installed(path: string, manifest: RuntimeManifest) {
  try {
    const saved = JSON.parse(await readFile(join(path, "installed.json"), "utf8"));
    await access(join(path, "python/bin/python3.12"));
    return saved.sha256 === manifest.sha256;
  } catch { return false; }
}
async function prepareStartupCache(path: string, finalPath: string, progress?: Options["progress"]) {
  const marker = join(path, ".startup-cache-ready");
  try { if ((await readFile(marker, "utf8")) === "1") return; } catch { /* Prepare once per installed runtime. */ }
  progress?.("Optimizing engine startup — first launch only…");
  // The archive deliberately excludes bytecode and build-machine filenames.
  // Compile locally once, using the final installation path in tracebacks.
  // -B workers still read this cache without writing more files on each search.
  await execute(join(path, "python/bin/python3.12"), ["-I", "-B", "-m", "compileall", "-q", "-j", "2",
    "-s", path, "-p", finalPath, join(path, "python/lib/python3.12")], { timeout: 120_000 });
  await writeFile(marker, "1", { mode: 0o600 });
}
export async function installRuntime(options: Options, manifest: RuntimeManifest) {
  validateManifest(manifest);
  const parent = join(options.storage, "runtime");
  const destination = join(parent, manifest.version);
  if (await installed(destination, manifest)) {
    await prepareStartupCache(destination, destination, options.progress);
    return destination;
  }
  await mkdir(parent, { recursive: true, mode: 0o700 });
  const lock = join(parent, "install.lock");
  let acquired = false;
  const deadline = Date.now() + 180_000;
  while (!acquired) {
    try { await mkdir(lock); acquired = true; }
    catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "EEXIST") throw error;
      if (await installed(destination, manifest)) return destination;
      const details = await stat(lock).catch(() => undefined);
      if (details && Date.now() - details.mtimeMs > 600_000) { await rm(lock, { recursive: true, force: true }); continue; }
      if (Date.now() > deadline) throw new Error("Engine setup is still running. Try opening the command again shortly.");
      options.progress?.("Another launcher is preparing the search engine…");
      await delay(500);
    }
  }
  const staging = join(parent, `.install-${process.pid}-${Date.now()}`);
  try {
    if (await installed(destination, manifest)) return destination;
    options.progress?.("Checking the bundled search engine…");
    const archive = join(options.assets, "runtime.zip");
    if (await digest(archive) !== manifest.sha256) throw new Error("The engine archive failed its integrity check. Download the installation ZIP again.");
    await mkdir(staging, { mode: 0o700 });
    options.progress?.("Preparing the search engine — first launch only…");
    await execute("/usr/bin/ditto", ["-x", "-k", archive, staging], { timeout: 180_000 });
    await access(join(staging, "python/bin/python3.12"));
    await execute(join(staging, "python/bin/python3.12"), ["-I", "-B", "-c", "import tinysearch, av, pypdfium2, fastapi; assert tinysearch.__version__ == '" + manifest.version + "'"], { timeout: 30_000 });
    await prepareStartupCache(staging, destination, options.progress);
    await writeFile(join(staging, "installed.json"), JSON.stringify(manifest), { mode: 0o600 });
    // Only a failed installation of this exact runtime version is replaceable.
    await rm(destination, { recursive: true, force: true });
    await rename(staging, destination);
    return destination;
  } finally {
    await rm(staging, { recursive: true, force: true });
    await rm(lock, { recursive: true, force: true });
  }
}
export async function ensureService(options: Options) {
  if (platform() !== "darwin" || arch() !== "arm64" || Number(release().split(".")[0]) < 25) {
    throw new Error("Semantic Search requires an Apple silicon Mac with macOS 26 or newer.");
  }
  let manifest: RuntimeManifest;
  try { manifest = validateManifest(JSON.parse(await readFile(join(options.assets, "runtime-manifest.json"), "utf8"))); }
  catch { throw new Error("This is a source build. Install the ready-to-use ZIP from the GitHub release."); }
  const base = `http://127.0.0.1:${options.port || 8766}`;
  const running = await info(base);
  if (running) {
    if (running.version !== manifest.version) throw new Error("An older search engine is running. Restart your Mac to finish updating the extension.");
    return base;
  }
  const runtime = await installRuntime(options, manifest);
  options.progress?.("Starting local search…");
  const logDirectory = join(options.storage, "logs");
  await mkdir(logDirectory, { recursive: true, mode: 0o700 });
  // Tinycast waits for spawned processes to finish. A short system-shell helper
  // backgrounds the engine with redirected descriptors and exits immediately.
  // Paths are positional arguments, never interpolated shell code.
  await execute("/bin/sh", ["-c", 'umask 077; /usr/bin/nohup "$1" -I -B -m tinysearch.runtime --storage "$2" --port "$3" </dev/null >>"$4" 2>&1 &',
    "semantic-search-start", join(runtime, "python/bin/python3.12"), options.storage,
    String(options.port || 8766), join(logDirectory, "service.log")], {
    timeout: 5000, cwd: runtime,
    env: { ...process.env, PYTHONNOUSERSITE: "1", PYTHONDONTWRITEBYTECODE: "1", HF_HUB_DISABLE_TELEMETRY: "1", TOKENIZERS_PARALLELISM: "false" },
  });
  const deadline = Date.now() + 30_000;
  while (Date.now() < deadline) {
    const ready = await info(base);
    if (ready?.version === manifest.version) return base;
    await delay(250);
  }
  throw new Error("The local engine could not start. Check free disk space and the service log in shared storage.");
}
