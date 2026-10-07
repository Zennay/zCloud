const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const source = fs.readFileSync(path.join(root, "public", "enhancements.js"), "utf8");
const now = Date.now();

function render({cached, history, range = "7d", progress = 50}) {
  const context = {
    localStorage: {
      getItem(key) {
        assert.equal(key, "zcloud:last-status:v1");
        return cached == null ? null : JSON.stringify(cached);
      },
    },
    Date,
    Number,
    JSON,
    String,
    Math,
    Object,
    Array,
    RegExp,
    console,
    HISTORY: {cloud: history || []},
    range,
    esc: value => String(value),
    num: value => String(value),
    icon: () => "",
    projectCard: () => '<article><div class="progress-row"></div></article>',
    overview: () => '<div class="dashboard-grid"></div>',
    detail: () => '<div class="detail-columns"></div>',
    infrastructure: () => "",
    resourcePanel: () => "",
    incidentPanel: () => "",
    document: {addEventListener() {}},
    fetch: async () => ({ok: true, json: async () => ({})}),
    AbortSignal: {timeout() { return undefined; }},
    $: () => ({hidden: true, textContent: ""}),
    DATA: {projects: []},
  };
  vm.runInNewContext(source, context, {filename: "enhancements.js"});
  return context.projectCard({id: "cloud", progress, quality: null});
}

const previous = {
  saved_at: now - 60 * 60 * 1000,
  data: {projects: [{id: "cloud", progress: 42}]},
};
let html = render({cached: previous, history: [{progress: 10}, {progress: 30}], progress: 50});
assert.match(html, /\+8 pp · last session/);
assert.match(html, /previous dashboard session/);
assert.doesNotMatch(html, /20 pp · 7d/);

const stale = {
  saved_at: now - (24 * 60 * 60 * 1000 + 1000),
  data: {projects: [{id: "cloud", progress: 42}]},
};
html = render({cached: stale, history: [{progress: 40}, {progress: 45}], progress: 50});
assert.match(html, /\+5 pp · 7d/);
assert.match(html, /loaded 7d/);
assert.doesNotMatch(html, /last session/);

html = render({cached: null, history: [{progress: 40}, {progress: 38}], range: "24h", progress: 50});
assert.match(html, /-2 pp · 24h/);

const malformed = {
  saved_at: now - 1000,
  data: {projects: [{id: "cloud", progress: 999}, {id: "../unsafe", progress: 1}]},
};
html = render({cached: malformed, history: [{progress: 20}, {progress: 20}], range: "30d", progress: 50});
assert.match(html, /0 pp · 30d/);
assert.doesNotMatch(html, /last session/);

html = render({cached: previous, history: [], progress: 50});
assert.match(html, /\+8 pp · last session/);

console.log("Project card progress session delta contract passed.");
