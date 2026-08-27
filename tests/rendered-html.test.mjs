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
  assert.match(html, /材料审核/);
  assert.match(html, /人机闭环/);
  assert.match(html, /材料齐套审核的三个架构版本/);
  assert.match(html, /先建立确定性 Workflow/);
  assert.match(html, /Send 并行匹配/);
  assert.match(html, /事实入口被阻断/);
  assert.match(html, /材料对齐被阻断/);
  assert.match(html, /只判断/);
  assert.match(html, /对应人员应提供的材料/);
  assert.doesNotMatch(html, /CASE-ZD-042/);
  assert.doesNotMatch(html, /Quick Tour|Deep Dive/);
  assert.doesNotMatch(html, /RECORDED|Mock|mock|未实现|可信度|个人展示|私下展示|有条件通过|最终审批/);
  assert.doesNotMatch(html, developmentPreviewMeta);
  assert.doesNotMatch(html, /react-loading-skeleton/);
});
