'use strict';
const fs = require('fs');
const path = require('path');

// Repo root = two levels up from tests/jsonata/lib
const REPO_ROOT = path.resolve(__dirname, '..', '..', '..');

/** Recursively list all *.asl.json files under a directory. */
function findAslFiles(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    if (entry.name === 'node_modules' || entry.name === '.git') continue;
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...findAslFiles(full));
    else if (entry.isFile() && entry.name.endsWith('.asl.json')) out.push(full);
  }
  return out;
}

/** Collect every {% ... %} JSONata expression in an object, with its JSON path. */
function collectExprs(obj, jsonPath, out) {
  if (typeof obj === 'string') {
    const s = obj.trim();
    if (s.startsWith('{%') && s.endsWith('%}')) {
      out.push({ path: jsonPath, expr: s.slice(2, -2).trim() });
    }
  } else if (Array.isArray(obj)) {
    obj.forEach((v, i) => collectExprs(v, `${jsonPath}[${i}]`, out));
  } else if (obj && typeof obj === 'object') {
    for (const k of Object.keys(obj)) collectExprs(obj[k], `${jsonPath}.${k}`, out);
  }
  return out;
}

module.exports = { REPO_ROOT, findAslFiles, collectExprs };
