const fs = require('fs');
const path = require('path');

const [root, outDir] = process.argv.slice(2);
if (!root || !outDir) throw new Error('usage: node flatten_bambu_profiles.js <BBL root> <output dir>');

const index = new Map();
function walk(dir) {
  for (const ent of fs.readdirSync(dir, {withFileTypes: true})) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walk(p);
    else if (ent.isFile() && ent.name.toLowerCase().endsWith('.json')) {
      try {
        const obj = JSON.parse(fs.readFileSync(p, 'utf8'));
        if (obj && typeof obj.name === 'string') index.set(obj.name, {obj, p});
        // Many base profiles are referred to by filename rather than by name.
        index.set(path.basename(p, '.json'), {obj, p});
      } catch (_) {}
    }
  }
}
walk(root);

function resolveName(name, stack = []) {
  const hit = index.get(name);
  if (!hit) throw new Error(`Missing profile dependency: ${name}`);
  if (stack.includes(hit.p)) throw new Error(`Profile cycle: ${[...stack, hit.p].join(' -> ')}`);
  return resolveObj(hit.obj, [...stack, hit.p]);
}

function resolveObj(obj, stack = []) {
  let merged = {};
  if (obj.inherits) merged = {...merged, ...resolveName(obj.inherits, stack)};
  const includes = Array.isArray(obj.include) ? obj.include : (obj.include ? [obj.include] : []);
  for (const inc of includes) merged = {...merged, ...resolveName(inc, stack)};
  merged = {...merged, ...obj};
  delete merged.inherits;
  delete merged.include;
  merged.from = 'User';
  return merged;
}

const picks = [
  ['machine', 'Bambu Lab H2S 0.4 nozzle', 'H2S_0.4_full.json'],
  ['process', '0.20mm Standard @BBL H2S', 'H2S_0.20_process_full.json'],
  ['filament', 'Generic PETG @BBL H2S', 'H2S_Generic_PETG_full.json'],
];

fs.mkdirSync(outDir, {recursive: true});
for (const [type, name, filename] of picks) {
  const full = resolveName(name);
  full.type = type;
  full.name = name;
  full.instantiation = 'true';
  const target = path.join(outDir, filename);
  fs.writeFileSync(target, JSON.stringify(full, null, 2));
  console.log(`${name} -> ${target} (${Object.keys(full).length} settings)`);
}
