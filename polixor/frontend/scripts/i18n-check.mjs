// בדיקת שלמות התרגום:
//   1. אותם מפתחות בעברית ובאנגלית (כולל צורות רבים _one/_other/_two).
//   2. כל מפתח קבוע שהקוד מבקש – t('a.b.c') – קיים בשתי השפות.
//   3. אין מחרוזת ריקה.
// הרצה: npm run i18n:check   (יוצא עם קוד 1 כשיש בעיה)

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative } from 'node:path'

const ROOT = new URL('..', import.meta.url).pathname
const LOCALES = join(ROOT, 'src/i18n/locales')
const LANGS = ['he', 'en']
const PLURAL = /_(zero|one|two|few|many|other)$/

function flatten(obj, prefix = '', out = {}) {
  for (const [k, v] of Object.entries(obj)) {
    const key = prefix ? `${prefix}.${k}` : k
    if (v && typeof v === 'object') flatten(v, key, out)
    else out[key] = v
  }
  return out
}

function load(lang) {
  const out = {}
  for (const f of readdirSync(join(LOCALES, lang))) {
    if (!f.endsWith('.json')) continue
    const ns = f.replace(/\.json$/, '')
    flatten(JSON.parse(readFileSync(join(LOCALES, lang, f), 'utf8')), ns, out)
  }
  return out
}

const cat = Object.fromEntries(LANGS.map((l) => [l, load(l)]))
const base = (k) => k.replace(PLURAL, '')
const problems = []

// 1 + 3
for (const a of LANGS) {
  for (const b of LANGS) {
    if (a === b) continue
    const bases = new Set(Object.keys(cat[b]).map(base))
    for (const k of Object.keys(cat[a])) {
      if (!bases.has(base(k))) problems.push(`[${b}] חסר מפתח: ${k}`)
    }
  }
  for (const [k, v] of Object.entries(cat[a])) {
    if (typeof v === 'string' && !v.trim()) problems.push(`[${a}] מחרוזת ריקה: ${k}`)
  }
}

// 2
function walk(dir, files = []) {
  for (const f of readdirSync(dir)) {
    const p = join(dir, f)
    if (statSync(p).isDirectory()) walk(p, files)
    else if (/\.(tsx?|jsx?)$/.test(f)) files.push(p)
  }
  return files
}
const used = new Map()
const re = /\bt\(\s*'([a-zA-Z0-9_.-]+)'/g
for (const file of walk(join(ROOT, 'src'))) {
  const src = readFileSync(file, 'utf8')
  for (const m of src.matchAll(re)) {
    if (!used.has(m[1])) used.set(m[1], relative(ROOT, file))
  }
}
for (const lang of LANGS) {
  const bases = new Set(Object.keys(cat[lang]).map(base))
  for (const [k, file] of used) {
    if (!bases.has(k)) problems.push(`[${lang}] המפתח '${k}' בשימוש ב-${file} אך לא קיים`)
  }
}

const counts = LANGS.map((l) => `${l}: ${Object.keys(cat[l]).length}`).join(', ')
if (problems.length) {
  console.error(problems.join('\n'))
  console.error(`\n✗ ${problems.length} בעיות תרגום (${counts})`)
  process.exit(1)
}
console.log(`✓ התרגום שלם – ${counts} מפתחות, ${used.size} מפתחות קבועים בשימוש בקוד`)
