// scan-hilog-crash-markers.cjs — reproducible crash-marker scan of a hilog capture
//
// Usage (from this directory):  node scan-hilog-crash-markers.cjs [hilog-full.txt]
//
// Case-sensitively counts the four crash/hang marker classes below over the
// WHOLE file (substring occurrences, not just line starts) and exits:
//   exit 0 -> every count is zero
//   exit 1 -> at least one count is nonzero (scan is the gate)
//   exit 2 -> the capture file could not be read
//
// No device, no network, no shell grep/findstr: runs on the preserved capture.
const fs = require('fs');
const path = require('path');

const MARKERS = ['FATAL', 'AppCrash', 'AppFreeze', 'JS_ERR'];
const target = process.argv[2] || 'hilog-full.txt';

let text;
try {
  text = fs.readFileSync(target, 'utf8');
} catch (e) {
  console.error('cannot read ' + target + ': ' + e.message);
  process.exit(2);
}

const lines = text.split(/\r?\n/);
const lineCount = text.endsWith('\n') ? lines.length - 1 : lines.length;

function countOccurrences(haystack, needle) {
  let n = 0;
  let i = haystack.indexOf(needle);
  while (i !== -1) {
    n++;
    i = haystack.indexOf(needle, i + needle.length);
  }
  return n;
}

console.log('file: ' + target);
console.log('lines: ' + lineCount);
console.log('case-sensitive substring occurrences:');

const nonzero = [];
for (const marker of MARKERS) {
  const n = countOccurrences(text, marker);
  console.log(marker + ': ' + n);
  if (n > 0) nonzero.push(marker);
}

if (nonzero.length > 0) {
  console.log('result: FAIL (' + nonzero.length + ' marker class(es) nonzero: ' + nonzero.join(', ') + ')');
  process.exit(1);
}
console.log('result: PASS (all ' + MARKERS.length + ' marker counts zero)');
