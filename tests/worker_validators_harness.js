// Loads the REAL validator functions out of both browser drivers and evaluates them on JSON cases
// from stdin. Used by tests/test_worker_prompt_contract.py so the server prompt can never drift
// away from what the clients require (that drift silently stopped every worker on 2026-10-01).
const fs = require("fs");
const path = require("path");
const root = path.resolve(__dirname, "..");

function extract(src, name) {
  const start = src.indexOf("function " + name + "(");
  if (start < 0) throw new Error("missing function " + name);
  let i = src.indexOf("{", start);
  let depth = 0;
  for (; i < src.length; i++) {
    const ch = src[i];
    if (ch === "{") depth++;
    else if (ch === "}") {
      depth--;
      if (depth === 0) return src.slice(start, i + 1);
    }
  }
  throw new Error("unbalanced braces in " + name);
}

const userscript = fs.readFileSync(path.join(root, "public/zcloud-worker.user.js"), "utf8");
const extension = fs.readFileSync(path.join(root, "firefox-extension/background.js"), "utf8");
const userscriptReady = new Function(extract(userscript, "assignmentReady") + "; return assignmentReady;")();
const userscriptDiagnostic = new Function(extract(userscript, "assignmentDiagnostic") + "; return assignmentDiagnostic;")();
const extensionReady = new Function(extract(extension, "portfolioAssignmentReady") + "; return portfolioAssignmentReady;")();

const input = JSON.parse(fs.readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({
  userscript: input.cases.map(c => !!userscriptReady(c)),
  extension: input.cases.map(c => !!extensionReady(c)),
  diagnostic: input.cases.map(c => userscriptDiagnostic(c)),
}));
