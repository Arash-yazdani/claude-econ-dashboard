// The PWA (public/app.js) and the home-screen widget (tools/macro-widget.js) each hand-maintain
// DEC2 / SIGNED formatting sets, and CLAUDE.md records that they drift with no automated check.
// This is that check. Run: node --test "tests/js/*.test.js"
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");

const grab = (file, name) => {
  const src = fs.readFileSync(file, "utf8");
  const m = src.match(new RegExp(`const ${name} = new Set\\(\\[([\\s\\S]*?)\\]\\)`));
  assert.ok(m, `${name} not found in ${file}`);
  const body = m[1].replace(/\/\/.*$/gm, "");           // strip inline comments
  return new Set([...body.matchAll(/"([a-z0-9_]+)"/g)].map((x) => x[1]));
};

// Deliberate differences, one per line with the reason. Anything else is drift.
const ALLOWED = {
  DEC2: new Set(["core_cpi_yoy"]),  // app: 2dp to stay level with the WHY-block basis line; widget: 1dp
  SIGNED: new Set([]),
};

for (const name of ["DEC2", "SIGNED"]) {
  test(`${name} matches between app.js and macro-widget.js`, () => {
    const app = grab("public/app.js", name);
    const widget = grab("tools/macro-widget.js", name);
    const diff = [...app].filter((id) => !widget.has(id)).concat([...widget].filter((id) => !app.has(id)))
      .filter((id) => !ALLOWED[name].has(id));
    assert.deepStrictEqual(diff, [], `${name} drift (add to ALLOWED with a reason if deliberate)`);
  });
}
