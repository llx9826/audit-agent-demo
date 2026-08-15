import assert from "node:assert/strict";
import test from "node:test";

const developmentPreviewMeta = /codex-preview["'][^>]*development/i;

async function worker() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  return (await import(workerUrl.href)).default;
}

const env = {
  ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) },
};
const ctx = { waitUntil() {}, passThroughOnException() {} };

test("server-renders the architecture-first audit console", async () => {
  const app = await worker();
  const response = await app.fetch(new Request("http://localhost/", { headers: { accept: "text/html" } }), env, ctx);
  assert.equal(response.status, 200);
  const html = await response.text();
  assert.match(html, /ARGUS/);
  assert.match(html, /架构演进/);
  assert.match(html, /协作执行/);
  assert.match(html, /证据闭环/);
  assert.match(html, /一笔宅抵贷/);
  assert.match(html, /Workflow 管状态/);
  assert.match(html, /CASE-ZD-042/);
  assert.doesNotMatch(html, /Quick Tour|Deep Dive/);
  assert.doesNotMatch(html, developmentPreviewMeta);
  assert.doesNotMatch(html, /react-loading-skeleton/);
});
