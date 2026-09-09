/* Build suppliers-data.js (window.GP_SUPPLIERS) and data/site-data.js (window.GP_SITE)
   from the CPHI_MILAN repository. Run:  node build-data.js
   The design HTML is NOT touched.

   GP_SITE.companies = the joined DATA from CPHI_MILAN/index.html (base)
                       UNION every supplier in ranking.jsonl missing from DATA,
                       reconstructed from profiles/companies/contacts/quotes/cphi. */
"use strict";
const fs = require("fs");
const path = require("path");

const REPO = "C:/Projects/Артем/CPHI_MILAN";
const OUT  = "C:/Projects/Артем/GetPostach";
const INDEX     = path.join(REPO, "index.html");
const RANKING   = path.join(REPO, "data/ranking.jsonl");
const PROFILES  = path.join(REPO, "data/profiles.jsonl");
const CONTACTS  = path.join(REPO, "data/contacts.jsonl");
const QUOTES    = path.join(REPO, "data/quotes.jsonl");
const COMPANIES = path.join(REPO, "data/companies.jsonl");
const CPHI      = path.join(REPO, "data/cphi-milan-2026-exhibitors.json");

// ---------- extract the joined DATA object from the old index.html ----------
function extractDATA() {
  const html = fs.readFileSync(INDEX, "utf8");
  const m = html.indexOf("const DATA =");
  if (m < 0) throw new Error("`const DATA =` not found in index.html");
  let i = html.indexOf("{", m);
  if (i < 0) throw new Error("DATA opening brace not found");
  const start = i;
  let depth = 0, inStr = false, q = "", esc = false;
  for (; i < html.length; i++) {
    const ch = html[i];
    if (inStr) {
      if (esc) { esc = false; continue; }
      if (ch === "\\") { esc = true; continue; }
      if (ch === q) inStr = false;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") { inStr = true; q = ch; continue; }
    if (ch === "{") depth++;
    else if (ch === "}") { depth--; if (depth === 0) { i++; break; } }
  }
  // eslint-disable-next-line no-eval
  return eval("(" + html.slice(start, i) + ")");
}

// ---------- helpers ----------
function readJSONL(file) {
  const out = [];
  const txt = fs.existsSync(file) ? fs.readFileSync(file, "utf8") : "";
  txt.split(/\r?\n/).forEach(l => { l = l.trim(); if (!l) return; try { out.push(JSON.parse(l)); } catch (e) {} });
  return out;
}
function groupByKey(rows, field) {
  const m = new Map();
  rows.forEach(r => { const k = r[field]; if (k == null) return; if (!m.has(k)) m.set(k, []); m.get(k).push(r); });
  return m;
}
function fnum(n) {
  if (n == null || isNaN(n)) return "";
  const r = Math.round(n * 100) / 100;
  return (r % 1 === 0 ? String(r) : parseFloat(r.toFixed(2)).toString());
}
function curSym(cur) {
  return cur === "EUR" ? "€" : cur === "USD" ? "$" : cur === "GBP" ? "£" : (cur ? cur + " " : "");
}
function monoOf(name) {
  const words = String(name || "").replace(/[^\p{L}\p{N}\s]/gu, " ").trim().split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  const w = words[0] || String(name || "");
  return w.slice(0, 2).toUpperCase();
}
function ratingOf(score) {
  const s = score || 0;
  if (s >= 97) return "4.9"; if (s >= 90) return "4.8"; if (s >= 85) return "4.7";
  if (s >= 79) return "4.6"; if (s >= 73) return "4.5"; if (s >= 67) return "4.4";
  if (s >= 61) return "4.3"; if (s >= 55) return "4.2"; if (s >= 49) return "4.1";
  return "4.0";
}
function catOf(products) {
  const s = (products || []).join(" ").toLowerCase();
  const food  = /extract|vitamin|amino|protein|inositol|collagen|nutrition|stevia|sweeten|spirulina|carnitine|betaine|taurine|creatine|glucosamine|chondroitin|herbal|berry|powder\b/g;
  const equip = /capsule|caps\b|\btube|packag|bottle|machine|equipment|\bfoil|laminat|closure|blister|carton/g;
  const f = (s.match(food) || []).length, e = (s.match(equip) || []).length;
  if (e > f && e >= 2) return "Обладнання";
  if (f >= 3) return "Харчова";
  return "Фарма";
}

// ---------- load sources ----------
console.log("Reading index.html DATA …");
const DATA = extractDATA();
const dataCompanies = DATA.companies || [];
const byKey = new Map(dataCompanies.map(c => [c.key, c]));
console.log("  DATA.companies:", dataCompanies.length);

console.log("Reading ranking / profiles / contacts / quotes / companies / cphi …");
let ranking = readJSONL(RANKING).filter(r => r.role !== "customer");
ranking.sort((a, b) => (b.score || 0) - (a.score || 0));
const profByKey     = new Map(readJSONL(PROFILES).map(p => [p.company, p]));
const contactsByCo  = groupByKey(readJSONL(CONTACTS), "company");
const quotesByCo    = groupByKey(readJSONL(QUOTES), "company");
const compRawByKey  = new Map(readJSONL(COMPANIES).map(c => [c.key, c]));
let exByKey = new Map();
try { (JSON.parse(fs.readFileSync(CPHI, "utf8")).exhibitors || []).forEach(e => exByKey.set(e.key, e)); } catch (e) {}
console.log("  ranking:", ranking.length, "| profiles:", profByKey.size, "| cphi:", exByKey.size);

// ---------- reconstruction from raw sources (for keys missing from DATA) ----------
function buildPeople(rows) {
  return (rows || []).map((c, i) => ({
    name: c.name || "", role: c.role || "", email: c.email || "",
    emails_alt: c.emails_alt || [], phone: c.phone || "", whatsapp: c.whatsapp || "",
    website: c.website || "", address: c.address || "", primary: i === 0
  }));
}
function buildPrices(rows) {
  const byProd = new Map();
  (rows || []).forEach(q => { const p = q.product; if (!p) return; if (!byProd.has(p)) byProd.set(p, []); byProd.get(p).push(q); });
  const groups = [];
  for (const [product, qs] of byProd) {
    const rs = qs.map(q => ({
      price: (typeof q.price === "number" ? q.price : null),
      date: q.date || "", spec: q.spec || "", inco: q.incoterms || "", basis: q.basis || "",
      cur: q.currency || "USD", unit: q.unit || "kg"
    })).sort((a, b) => String(a.date).localeCompare(String(b.date)));
    const nums = rs.filter(r => typeof r.price === "number").map(r => r.price);
    groups.push({
      product, cur: (rs[0] && rs[0].cur) || "USD", unit: (rs[0] && rs[0].unit) || "kg",
      min: nums.length ? Math.min(...nums) : null, max: nums.length ? Math.max(...nums) : null,
      n: rs.length, rows: rs
    });
  }
  return groups;
}
function exhibitOf(key) {
  const e = exByKey.get(key);
  return e ? { hall: e.hall || "", stand: e.stand || "", purpose: e.purpose || "" } : undefined;
}
// returns {people, prices, exhibit, products} for a key — from DATA if present, else reconstructed
function resolve(key) {
  const c = byKey.get(key);
  if (c) return { people: c.people || [], prices: c.prices || [], exhibit: c.exhibit, products: c.products || [] };
  return {
    people: buildPeople(contactsByCo.get(key)),
    prices: buildPrices(quotesByCo.get(key)),
    exhibit: exhibitOf(key),
    products: (profByKey.get(key) || {}).products || []
  };
}
// full GP_SITE company record for a ranking supplier missing from DATA
function reconstructCompany(r) {
  const prof = profByKey.get(r.key) || {};
  const craw = compRawByKey.get(r.key) || {};
  const res = resolve(r.key);
  const inb = r.inbound != null ? r.inbound : (craw.inbound || 0);
  const outb = r.outbound != null ? r.outbound : (craw.outbound || 0);
  const rec = {
    key: r.key,
    name: r.name || prof.name || r.key,
    country: r.country || prof.country || "",
    type: r.type_label || "",
    status: r.status || "",
    inb, outb, msgs: inb + outb,
    threads: r.threads != null ? r.threads : (craw.n_threads || 0),
    quotes: r.quotes != null ? r.quotes : 0,
    npeople: res.people.length,
    first: r.first || craw.first || "",
    last: r.last || craw.last || "",
    gap: r.gap_days != null ? r.gap_days : null,
    who: { type_reason: prof.type_reason || "", summary: prof.summary || "", domains: prof.domains || [] },
    people: res.people,
    prices: res.prices,
    products: r.products || prof.products || []
  };
  if (res.exhibit) rec.exhibit = res.exhibit;
  return rec;
}

// ---------- GP_SITE = DATA companies UNION missing ranking suppliers ----------
const missing = ranking.filter(r => !byKey.has(r.key)).map(reconstructCompany);
const siteCompanies = dataCompanies.concat(missing);
const maxMsgs = Math.max(1, ...siteCompanies.map(c => c.msgs || 0));
console.log("  GP_SITE companies:", siteCompanies.length, "(DATA", dataCompanies.length, "+ merged", missing.length, ")");

// ---------- GP_SUPPLIERS from ranking (positions/contacts via resolve) ----------
function positionsOf(prices) {
  const out = [];
  (prices || []).forEach(g => {
    const rows = (g.rows || []).filter(r => typeof r.price === "number" && r.date)
      .sort((a, b) => String(a.date).localeCompare(String(b.date)));
    if (!rows.length) return;
    const hist = rows.map(r => ({ m: String(r.date).slice(2, 7), p: r.price }));
    const first = hist[0].p, last = hist[hist.length - 1].p;
    const pct = first ? Math.round((last - first) / first * 100) : 0;
    out.push({
      name: g.product, price: curSym(g.cur || "USD") + fnum(last) + " / " + (g.unit || "kg"),
      delta: (pct > 0 ? "+" : "") + pct + "%", up: last > first, hist
    });
  });
  return out;
}
const suppliers = ranking.map((r, idx) => {
  const res = resolve(r.key);
  const person = (res.people || []).find(p => p.primary) || (res.people || [])[0] || null;
  const ex = res.exhibit;
  return {
    id: idx + 1, key: r.key, name: r.name || r.key, mono: monoOf(r.name || r.key),
    country: (r.country || "") + (r.type_label ? " · " + r.type_label : ""),
    cat: catOf(r.products || res.products || []),
    type: r.type_label || "", status: r.status || "У базі",
    score: r.score || 0, rating: ratingOf(r.score),
    us: r.outbound != null ? r.outbound : 0, them: r.inbound != null ? r.inbound : 0,
    threads: r.threads || 0, quotes: r.quotes || 0,
    manager: person ? (person.name || "") : "", email: person ? (person.email || "") : "", phone: person ? (person.phone || "") : "",
    site: r.key,
    cphi: ex ? ("CPHI Milan · Hall " + (ex.hall || "") + " · " + (ex.stand || "")) : "",
    products: r.products || res.products || [],
    positions: positionsOf(res.prices)
  };
});
console.log("  GP_SUPPLIERS:", suppliers.length, "| with price history:", suppliers.filter(s => s.positions.length).length);

// ---------- write ----------
const siteOut = path.join(OUT, "data/site-data.js");
fs.mkdirSync(path.dirname(siteOut), { recursive: true });
fs.writeFileSync(siteOut, "window.GP_SITE = " + JSON.stringify({ companies: siteCompanies, maxMsgs }) + ";\n", "utf8");
console.log("Wrote", siteOut, fs.statSync(siteOut).size, "bytes");

const supOut = path.join(OUT, "suppliers-data.js");
fs.writeFileSync(supOut, "window.GP_SUPPLIERS = " + JSON.stringify(suppliers) + ";\n", "utf8");
console.log("Wrote", supOut, fs.statSync(supOut).size, "bytes");

console.log("DONE.");
