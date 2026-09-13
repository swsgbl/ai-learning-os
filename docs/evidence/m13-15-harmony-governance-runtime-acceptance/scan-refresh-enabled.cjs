// scan-refresh-enabled.cjs — reproducible scan of the refresh-button enabled state
// Usage: node scan-refresh-enabled.cjs <dir|file...>
// Walks preserved uiautomator JSON captures and prints each Button whose text
// contains 刷新 (整体刷新 header control and per-section 刷新 controls),
// with its `enabled` / `visible` attributes. Run with no network, no device.
const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
let files = [];
for (const a of args) {
  const st = fs.statSync(a);
  if (st.isDirectory()) {
    files = files.concat(fs.readdirSync(a).filter(f => /^governance-.*\.json$/.test(f)).map(f => path.join(a, f)));
  } else {
    files.push(a);
  }
}

function walk(node, out) {
  const at = (node && node.attributes) || {};
  if (at.type === 'Button' && /刷新/.test(at.text || at.originalText || '')) {
    out.push({ id: at.accessibilityId, text: (at.text || at.originalText || '').trim(), enabled: at.enabled, visible: at.visible });
  }
  for (const c of (node.children || [])) walk(c, out);
}

for (const f of files.sort()) {
  let tree;
  try { tree = JSON.parse(fs.readFileSync(f, 'utf8')); }
  catch (e) { console.log('=== ' + f + '\nPARSE ERROR: ' + e.message); continue; }
  const found = [];
  walk(tree, found);
  console.log('=== ' + path.basename(f));
  for (const b of found) console.log(JSON.stringify(b));
  if (!found.length) console.log('(no refresh Button found)');
}
