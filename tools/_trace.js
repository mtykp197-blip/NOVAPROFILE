const fs = require('fs');
const s = fs.readFileSync('C:/Users/ezycloudx-admin/Desktop/NOVAPROFILE/tools/_audit_script.js', 'utf8');
const traced = s.replace(
  'const rec = (name, fn) => {',
  'const rec = (name, fn) => { console.error("CALL " + name);'
);
const fn = eval('(' + traced + ')');
try {
  const out = fn();
  console.error('KEYS ' + Object.keys(out).length);
} catch (e) {
  console.error('THROWN ' + e.message);
}
