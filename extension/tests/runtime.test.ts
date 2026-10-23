import { test } from "node:test";
import assert from "node:assert/strict";
import {
  mkdtemp,
  mkdir,
  readFile,
  writeFile,
  access,
  rm,
  chmod,
  readdir,
  stat,
  utimes,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import {
  BackgroundNotReady,
  CompanionRequired,
  digest,
  distribution,
  installRuntime,
  preparedRuntime,
  stopRunningService,
  validateManifest,
} from "../src/runtime";
import { automaticEnabled } from "../src/automatic-client";

test("disabled or paused automatic updates do not create storage or activate a service", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic-automatic-off-"));
  const storage = join(root, "storage");
  try {
    assert.equal(await automaticEnabled(storage), false);
    await assert.rejects(access(storage));
    await mkdir(storage);
    const file = join(storage, "automatic-indexing.json");
    for (const value of [
      "invalid json",
      "null",
      "[]",
      JSON.stringify({ enabled: false, paused: false }),
      JSON.stringify({ enabled: true, paused: true }),
      JSON.stringify({ enabled: 1, paused: false }),
    ]) {
      await writeFile(file, value);
      assert.equal(await automaticEnabled(storage), false);
    }
    await writeFile(file, JSON.stringify({ enabled: true, paused: false }));
    assert.equal(await automaticEnabled(storage), true);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("background checks only accept a prepared runtime and never install or compile it", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-background-ready-"));
  try {
    const options = { storage, assets: join(storage, "nonexistent-assets") };
    await assert.rejects(
      preparedRuntime(options, manifest),
      BackgroundNotReady,
    );
    const runtime = join(storage, "runtime", manifest.version);
    await mkdir(join(runtime, "python/bin"), { recursive: true });
    await writeFile(join(runtime, "installed.json"), JSON.stringify(manifest));
    await writeFile(join(runtime, "python/bin/python3.12"), "not executed");
    await assert.rejects(
      preparedRuntime(options, manifest),
      BackgroundNotReady,
    );
    await writeFile(join(runtime, ".startup-cache-ready"), "1");
    assert.equal(await preparedRuntime(options, manifest), runtime);
    assert.deepEqual((await readdir(runtime)).sort(), [
      ".startup-cache-ready",
      "installed.json",
      "python",
    ]);
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

const manifest = {
  version: "0.2.0",
  sha256: "0".repeat(64),
  platform: "darwin-arm64" as const,
  unpacked_bytes: 100,
};
test("rejects malformed runtime manifests", () => {
  assert.throws(() =>
    validateManifest({ ...manifest, version: "../../models" }),
  );
  assert.throws(() => validateManifest({ ...manifest, sha256: "invalid" }));
});
test("Store mode never falls back to installing an executable archive", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic-search-companion-"));
  try {
    const assets = join(root, "assets"),
      storage = join(root, "storage");
    await mkdir(assets);
    await mkdir(join(storage, "data"), { recursive: true });
    await writeFile(join(storage, "data/index.sqlite3"), "preserve");
    await writeFile(
      join(assets, "distribution.json"),
      JSON.stringify({
        mode: "companion",
        installer_url:
          "https://github.com/nikusti/semantic-search/releases/tag/v0.2.0",
      }),
    );
    // Even if an archive is present, a Store build must not execute it.
    await writeFile(join(assets, "runtime.zip"), "not executable");
    await assert.rejects(
      installRuntime({ assets, storage }, manifest),
      CompanionRequired,
    );
    assert.equal(
      await readFile(join(storage, "data/index.sqlite3"), "utf8"),
      "preserve",
    );
    await assert.rejects(access(join(storage, "runtime/install.lock")));
    await writeFile(
      join(assets, "distribution.json"),
      JSON.stringify({
        mode: "companion",
        installer_url: "https://example.com/engine",
      }),
    );
    await assert.rejects(distribution(assets), /installer link/);
    await writeFile(
      join(assets, "distribution.json"),
      JSON.stringify({ mode: "download" }),
    );
    await assert.rejects(distribution(assets), /package/);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
test("corrupt archive leaves models and indexes untouched and releases the lock", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic-search-test-"));
  try {
    const assets = join(root, "assets"),
      storage = join(root, "storage");
    await mkdir(assets);
    await mkdir(join(storage, "data"), { recursive: true });
    await mkdir(join(storage, "models"));
    await writeFile(join(storage, "data", "index.sqlite3"), "preserve");
    await writeFile(join(storage, "models", "weight"), "preserve");
    await writeFile(join(assets, "runtime.zip"), "corrupt");
    assert.notEqual(await digest(join(assets, "runtime.zip")), manifest.sha256);
    await assert.rejects(
      installRuntime({ assets, storage }, manifest),
      /integrity/,
    );
    assert.equal(
      await readFile(join(storage, "data", "index.sqlite3"), "utf8"),
      "preserve",
    );
    assert.equal(
      await readFile(join(storage, "models", "weight"), "utf8"),
      "preserve",
    );
    await assert.rejects(access(join(storage, "runtime", "install.lock")));
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
test("an installed runtime is reused without unpacking or needing the archive", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-search-test-"));
  try {
    const runtime = join(storage, "runtime", manifest.version);
    await mkdir(join(runtime, "python/bin"), { recursive: true });
    await writeFile(
      join(runtime, "python/bin/python3.12"),
      "#!/bin/sh\nexit 0\n",
    );
    await chmod(join(runtime, "python/bin/python3.12"), 0o755);
    await writeFile(join(runtime, "installed.json"), JSON.stringify(manifest));
    await writeFile(join(runtime, ".startup-cache-ready"), "1");
    assert.equal(
      await installRuntime({ assets: "/unneeded", storage }, manifest),
      runtime,
    );
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

test("an installed runtime prepares its local startup cache once and preserves user data", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-search-cache-test-"));
  try {
    const runtime = join(storage, "runtime", manifest.version);
    const executable = join(runtime, "python/bin/python3.12");
    const log = join(runtime, "compile.log");
    await mkdir(join(runtime, "python/bin"), { recursive: true });
    await mkdir(join(storage, "data"));
    await mkdir(join(storage, "models"));
    await writeFile(join(storage, "data/index.sqlite3"), "preserve");
    await writeFile(join(storage, "models/weights"), "preserve");
    await writeFile(join(runtime, "installed.json"), JSON.stringify(manifest));
    await writeFile(
      executable,
      `#!${process.execPath}\nrequire('node:fs').appendFileSync(${JSON.stringify(log)},JSON.stringify(process.argv.slice(2))+'\\n');\n`,
    );
    await chmod(executable, 0o755);
    for (let i = 0; i < 2; i++)
      assert.equal(
        await installRuntime({ assets: "/unneeded", storage }, manifest),
        runtime,
      );
    const calls = (await readFile(log, "utf8"))
      .trim()
      .split("\n")
      .map((line) => JSON.parse(line) as string[]);
    assert.equal(calls.filter((args) => args.includes("compileall")).length, 1);
    assert.equal(
      calls.filter((args) => args.includes("tinysearch.coreml_assets")).length,
      2,
    );
    assert.equal(
      await readFile(join(runtime, ".startup-cache-ready"), "utf8"),
      "1",
    );
    assert.equal(
      await readFile(join(storage, "data/index.sqlite3"), "utf8"),
      "preserve",
    );
    assert.equal(
      await readFile(join(storage, "models/weights"), "utf8"),
      "preserve",
    );
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

import { addExclusionFolders, reconcileValues } from "../src/settings-values";

test("another launcher refreshes untouched settings without discarding local edits", () => {
  const initial = {
    roots: "~/Documents",
    excluded_paths: "~/Documents/private",
    dimensions: "512",
  };
  const external = {
    ...initial,
    excluded_paths: "~/Documents/private\n~/Documents/another-folder",
  };
  assert.deepEqual(reconcileValues(initial, initial, external), external);
  const draft = { ...initial, dimensions: "256" };
  assert.deepEqual(reconcileValues(draft, initial, external), {
    ...external,
    dimensions: "256",
  });
  const exclusionDraft = {
    ...initial,
    excluded_paths: "~/Documents/local-draft",
  };
  assert.equal(
    reconcileValues(exclusionDraft, initial, external).excluded_paths,
    exclusionDraft.excluded_paths,
  );
});

test("a native picker selection adds exclusions without replacing earlier folders", () => {
  const saved = ["~/Documents/private", "~/Pictures/private"];
  assert.deepEqual(addExclusionFolders(saved, ["~/Downloads/private"]), [
    ...saved,
    "~/Downloads/private",
  ]);
  assert.deepEqual(addExclusionFolders(saved, ["~/Documents/private"]), saved);
  assert.deepEqual(addExclusionFolders(saved, []), saved);
});

import { fileURLToPath } from "node:url";
import { createPreviewFiles } from "../src/preview-files";

test("preview files are private, bounded, validated and removed on close", async () => {
  const root = await mkdtemp(join(tmpdir(), "semantic search preview-"));
  const image =
    "data:image/jpeg;base64," +
    Buffer.from([0xff, 0xd8, 0xff, 0xd9]).toString("base64");
  try {
    const cache = await createPreviewFiles(root);
    const reference = await cache.write(image);
    const path = fileURLToPath(reference);
    assert.ok(path.startsWith(join(root, "preview-cache/session-")));
    assert.equal((await stat(path)).mode & 0o777, 0o600);
    assert.deepEqual(
      await readFile(path),
      Buffer.from([0xff, 0xd8, 0xff, 0xd9]),
    );
    await assert.rejects(cache.write("file:///private/data.jpg"), /Invalid/);
    await assert.rejects(cache.write("data:image/jpeg;base64,YmFk"), /JPEG/);
    await assert.rejects(
      cache.write("data:image/jpeg;base64," + "A".repeat(8 * 1024 * 1024 + 1)),
      /Invalid/,
    );
    await cache.close();
    await assert.rejects(access(path));
    await assert.rejects(cache.write(image), /closed/);
    const stale = join(root, "preview-cache/session-expired");
    const unrelated = join(root, "preview-cache/preserve");
    await mkdir(stale);
    await mkdir(unrelated);
    await utimes(stale, new Date(0), new Date(0));
    const next = await createPreviewFiles(root);
    await assert.rejects(access(stale));
    await access(unrelated);
    await next.close();
    assert.deepEqual(await readdir(join(root, "preview-cache")), ["preserve"]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Tinycast keeps its supported inline preview format without writing a cache", async () => {
  const support = "/unneeded/com.tinycast.app/extension-support/example";
  const cache = await createPreviewFiles(support);
  const image = "data:image/jpeg;base64,/9j/2Q==";
  assert.equal(await cache.write(image), image);
  await cache.close();
  await assert.rejects(access(support));
});

import { symlink } from "node:fs/promises";
import { createServer } from "node:http";
import { execFileSync } from "node:child_process";
import {
  configureEngineToken,
  engineFetch,
  engineCurlFetch,
  engineTokenHeader,
  EngineTokenMissing,
  readEngineToken,
} from "../src/engine-auth";
import { validExtensionName } from "../src/launcher-probe";

test("launcher identity accepts legacy and Store names, and rejects unrelated packages", () => {
  assert.equal(validExtensionName("semantic-search"), true);
  assert.equal(validExtensionName("semantic-file-search"), true);
  assert.equal(validExtensionName("other-extension"), false);
  assert.equal(validExtensionName(undefined), false);
});

test("engine token reading fails closed for missing, public, malformed and symlink files", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-auth-file-"));
  const path = join(storage, "auth-token");
  const token = "a".repeat(64);
  try {
    await assert.rejects(readEngineToken(storage), /Install or start/);
    await writeFile(path, token, { mode: 0o600 });
    assert.equal(await readEngineToken(storage), token);
    await chmod(path, 0o644);
    await assert.rejects(readEngineToken(storage), /private/);
    await chmod(path, 0o600);
    await writeFile(path, "not a token");
    await assert.rejects(readEngineToken(storage), /invalid/);
    await writeFile(path, token);
    await symlink(path, join(storage, "linked"));
    await rm(path);
    await symlink(join(storage, "linked"), path);
    await assert.rejects(readEngineToken(storage), /private/);
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

test("engine requests authenticate every local API call, preserve request data and reread rotated tokens", async (t) => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-auth-requests-"));
  const path = join(storage, "auth-token");
  const tokens = ["b".repeat(64), "c".repeat(64)];
  const calls: { input: string; init: RequestInit }[] = [];
  let unauthorized = false;
  t.mock.method(
    globalThis,
    "fetch",
    async (input: string, init: RequestInit) => {
      calls.push({ input, init });
      return new Response("{}", { status: unauthorized ? 401 : 200 });
    },
  );
  try {
    await writeFile(path, tokens[0], { mode: 0o600 });
    for (const endpoint of [
      "search",
      "status",
      "preview",
      "photos/preview",
      "settings",
      "automatic/tick",
      "session",
      "engine/stop",
    ]) {
      await engineFetch(
        `http://127.0.0.1:8766/v1/${endpoint}`,
        {
          method: "POST",
          body: '{"query":"local"}',
          headers: {
            "Content-Type": "application/json",
            "x-semantic-search-token": "untrusted",
          },
          redirect: "follow",
        },
        storage,
      );
    }
    for (const { init } of calls) {
      const headers = new Headers(init.headers);
      assert.equal(headers.get(engineTokenHeader), tokens[0]);
      assert.equal(headers.get("Content-Type"), "application/json");
      assert.equal(init.body, '{"query":"local"}');
      assert.equal(init.method, "POST");
      assert.equal(init.redirect, "error");
    }
    await writeFile(path, tokens[1]);
    await engineFetch(
      "http://localhost:8766/v1/status",
      { headers: new Headers({ Accept: "application/json" }) },
      storage,
    );
    assert.equal(
      new Headers(calls.at(-1)?.init.headers).get(engineTokenHeader),
      tokens[1],
    );
    assert.equal(
      new Headers(calls.at(-1)?.init.headers).get("Accept"),
      "application/json",
    );
    const count = calls.length;
    for (const url of [
      "https://huggingface.co/model/weights",
      "http://example.com/v1/status",
      "http://user:secret@localhost:8766/v1/status",
    ])
      await assert.rejects(engineFetch(url, {}, storage), /local HTTP/);
    assert.equal(
      calls.length,
      count,
      "remote requests never receive the engine credential",
    );
    unauthorized = true;
    await assert.rejects(
      engineFetch("http://localhost:8766/v1/status", {}, storage),
      /rejected its authentication/,
    );
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

test("an engine redirect cannot forward its credential to another server", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-auth-redirect-"));
  let leaked = false;
  const destination = createServer((_request, response) => {
    leaked = true;
    response.end("unexpected");
  });
  const source = createServer((_request, response) => {
    const port = (destination.address() as { port: number }).port;
    response.writeHead(307, { Location: `http://127.0.0.1:${port}/weights` });
    response.end();
  });
  try {
    await writeFile(join(storage, "auth-token"), "d".repeat(64), {
      mode: 0o600,
    });
    await new Promise<void>((resolve) =>
      destination.listen(0, "127.0.0.1", resolve),
    );
    await new Promise<void>((resolve) =>
      source.listen(0, "127.0.0.1", resolve),
    );
    const port = (source.address() as { port: number }).port;
    await assert.rejects(
      engineFetch(`http://127.0.0.1:${port}/v1/search`, {}, storage),
    );
    assert.equal(leaked, false);
  } finally {
    source.closeAllConnections();
    destination.closeAllConnections();
    await Promise.all([
      new Promise<void>((resolve) => source.close(() => resolve())),
      new Promise<void>((resolve) => destination.close(() => resolve())),
    ]);
    await rm(storage, { recursive: true, force: true });
  }
});

test("authenticated runtime stop fails closed when its token is missing", async (t) => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-auth-upgrade-"));
  let requests = 0;
  t.mock.method(globalThis, "fetch", async () => {
    requests++;
    return new Response("{}");
  });
  try {
    for (const version of ["0.7.1", "0.6.1", "0.7.2", "unexpected"])
      await assert.rejects(
        stopRunningService("http://127.0.0.1:8766", storage, version),
        EngineTokenMissing,
      );
    await assert.rejects(
      stopRunningService("http://127.0.0.1:8766", storage, "0.4.1"),
      /one-time restart of your Mac/,
    );
    assert.equal(requests, 0);
    await writeFile(join(storage, "auth-token"), "e".repeat(64), {
      mode: 0o600,
    });
    await stopRunningService("http://127.0.0.1:8766", storage, "0.7.1");
    assert.equal(requests, 1);
  } finally {
    await rm(storage, { recursive: true, force: true });
  }
});

test("verified published legacy runtimes can stop without creating or sending an auth token", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-legacy-upgrade-"));
  let requests = 0;
  const server = createServer((request, response) => {
    requests++;
    assert.equal(request.url, "/v1/engine/stop");
    assert.equal(request.method, "POST");
    assert.equal(request.headers[engineTokenHeader.toLowerCase()], undefined);
    response.end("{}");
  });
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const port = (server.address() as { port: number }).port;
    const published = [
      "0.5.0",
      "0.5.1",
      "0.6.0",
      "0.6.2",
      "0.6.3",
      "0.6.4",
      "0.6.5",
      "0.7.0",
    ];
    for (const version of published)
      assert.equal(
        (await stopRunningService(`http://127.0.0.1:${port}`, storage, version))
          .status,
        200,
      );
    assert.equal(requests, published.length);
    await assert.rejects(access(join(storage, "auth-token")));
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    await rm(storage, { recursive: true, force: true });
  }
});

test("custom service tokens use the chosen file while managed runtimes retain their own storage", async (t) => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-custom-token-"));
  const custom = join(storage, "custom-token");
  const seen: string[] = [];
  t.mock.method(
    globalThis,
    "fetch",
    async (_input: string, init: RequestInit) => {
      seen.push(new Headers(init.headers).get(engineTokenHeader) || "");
      return new Response("{}");
    },
  );
  const base = "http://127.0.0.1:18788";
  try {
    await writeFile(custom, "1".repeat(64), { mode: 0o600 });
    await writeFile(join(storage, "auth-token"), "2".repeat(64), {
      mode: 0o600,
    });
    configureEngineToken(base, custom);
    await engineFetch(`${base}/v1/search`);
    await engineFetch(`${base}/v1/status`, {}, storage);
    assert.deepEqual(seen, ["1".repeat(64), "2".repeat(64)]);
    assert.throws(
      () => configureEngineToken(base, "relative/auth-token"),
      /absolute path/,
    );
    configureEngineToken(base, "");
  } finally {
    configureEngineToken(base, "");
    await rm(storage, { recursive: true, force: true });
  }
});

test("Tinycast curl transport authenticates without credentials in argv and cleans private request files", async () => {
  const storage = await mkdtemp(join(tmpdir(), "semantic-auth-curl-"));
  const token = "f".repeat(64);
  const bodies: string[] = [];
  const server = createServer((request, response) => {
    assert.equal(request.headers[engineTokenHeader.toLowerCase()], token);
    const processes = execFileSync("/bin/ps", ["-ax", "-o", "command="], {
      encoding: "utf8",
    });
    assert.equal(
      processes.includes(token),
      false,
      "credentials do not appear in process arguments",
    );
    assert.equal(request.headers["content-type"], "application/json");
    const parts: Buffer[] = [];
    request.on("data", (chunk) => parts.push(Buffer.from(chunk)));
    request.on("end", () => {
      bodies.push(Buffer.concat(parts).toString());
      response.writeHead(request.url === "/v1/redirect" ? 307 : 200, {
        "Content-Type": "application/json",
        Location: "/v1/leaked",
      });
      response.end('{"ok":true}');
    });
  });
  const before = new Set(
    (await readdir(tmpdir())).filter((name) =>
      name.startsWith("semantic-engine-request-"),
    ),
  );
  Object.defineProperty(process.versions, "tinycast", {
    value: "test",
    configurable: true,
  });
  try {
    await writeFile(join(storage, "auth-token"), token, { mode: 0o600 });
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const port = (server.address() as { port: number }).port;
    const start = performance.now();
    for (let index = 0; index < 3; index++) {
      const response = await engineFetch(
        `http://127.0.0.1:${port}/v1/search`,
        {
          method: "POST",
          headers: {
            [engineTokenHeader]: token,
            "Content-Type": "application/json",
          },
          body: '{"query":"quotes \\" and a newline \\n"}',
        },
        storage,
      );
      assert.deepEqual(await response.json(), { ok: true });
    }
    console.log(
      `Tinycast secure curl transport: ${((performance.now() - start) / 3).toFixed(1)} ms/request`,
    );
    assert.equal(bodies.length, 3);
    assert.ok(bodies.every((body) => body === bodies[0]));
    await assert.rejects(
      engineCurlFetch(`http://127.0.0.1:${port}/v1/redirect`, {
        headers: {
          [engineTokenHeader]: token,
          "Content-Type": "application/json",
        },
      }),
      /unexpected redirect/,
    );
    assert.equal(bodies.length, 4, "redirect destination was never requested");
    await assert.rejects(
      engineCurlFetch(`http://127.0.0.1:${port}/v1/search`, {
        headers: { "Bad\nHeader": "value" },
      }),
      /Invalid engine request header/,
    );
    const after = (await readdir(tmpdir())).filter(
      (name) =>
        name.startsWith("semantic-engine-request-") && !before.has(name),
    );
    assert.deepEqual(after, []);
  } finally {
    delete (process.versions as NodeJS.ProcessVersions & { tinycast?: string })
      .tinycast;
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
    await rm(storage, { recursive: true, force: true });
  }
});

test("canceling a Tinycast engine request terminates curl and removes private temporary files", async () => {
  let accepted!: () => void;
  const pending = new Promise<void>((resolve) => {
    accepted = resolve;
  });
  const server = createServer((_request, _response) => {
    accepted();
  });
  const before = new Set(
    (await readdir(tmpdir())).filter((name) =>
      name.startsWith("semantic-engine-request-"),
    ),
  );
  try {
    await new Promise<void>((resolve) =>
      server.listen(0, "127.0.0.1", resolve),
    );
    const port = (server.address() as { port: number }).port;
    const controller = new AbortController();
    const request = engineCurlFetch(`http://127.0.0.1:${port}/v1/preview`, {
      signal: controller.signal,
    });
    await pending;
    controller.abort(new Error("selection changed"));
    await assert.rejects(request, /selection changed/);
    const after = (await readdir(tmpdir())).filter(
      (name) =>
        name.startsWith("semantic-engine-request-") && !before.has(name),
    );
    assert.deepEqual(after, []);
  } finally {
    server.closeAllConnections();
    await new Promise<void>((resolve) => server.close(() => resolve()));
  }
});
