/* Build suppliers-data.js (window.GP_SUPPLIERS) and data/site-data.js (window.GP_SITE)
   from the CPHI_MILAN repository. Run:  node build-data.js
   The design HTML is NOT touched. */
"use strict";
const fs = require("fs");
const path = require("path");

const REPO = "C:/Projects/Артем/CPHI_MILAN";
const OUT  = "C:/Projects/Артем/GetPostach";
const INDEX   = path.join(REPO, "index.html");
const RANKING = path.join(REPO, "data/ranking.jsonl");

// ---------- 1. extract the joined DATA object from the old index.html ----------
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
  const literal = html.slice(start, i);
  // eval the object literal (data only – no code)
  // eslint-disable-next-line no-eval
  const DATA = eval("(" + literal + ")");
  return DATA;
}

// ---------- helpers ----------
function readJSONL(file) {
  return fs.readFileSync(file, "utf8").split(/\r?\n/).filter(Boolean).map(l => JSON.parse(l));
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
function positionsFromCompany(company) {
  if (!company || !Array.isArray(company.prices)) return [];
  const out = [];
  company.prices.forEach(g => {
    const rows = (g.rows || []).filter(r => typeof r.price === "number" && r.date)
      .sort((a, b) => String(a.date).localeCompare(String(b.date)));
    if (!rows.length) return;
    const hist = rows.map(r => ({ m: String(r.date).slice(2, 7), p: r.price }));
    const unit = g.unit || "kg", sym = curSym(g.cur || "USD");
    const first = hist[0].p, last = hist[hist.length - 1].p;
    const pct = first ? Math.round((last - first) / first * 100) : 0;
    out.push({
      name: g.product,
      price: sym + fnum(last) + " / " + unit,
      delta: (pct > 0 ? "+" : "") + pct + "%",
      up: last > first,
      hist
    });
  });
  return out;
}
function primaryPerson(company) {
  if (!company || !Array.isArray(company.people) || !company.people.length) return null;
  return company.people.find(p => p.primary) || company.people[0];
}

// ---------- 2. build ----------
console.log("Reading index.html DATA …");
const DATA = extractDATA();
const companies = DATA.companies || [];
const maxMsgs = DATA.maxMsgs || Math.max(1, ...companies.map(c => c.msgs || 0));
console.log("  DATA.companies:", companies.length, "| maxMsgs:", maxMsgs);
const byKey = new Map(companies.map(c => [c.key, c]));

console.log("Reading ranking.jsonl …");
let ranking = readJSONL(RANKING);
ranking = ranking.filter(r => r.role !== "customer");
ranking.sort((a, b) => (b.score || 0) - (a.score || 0));
console.log("  ranking rows:", ranking.length);

const suppliers = ranking.map((r, idx) => {
  const c = byKey.get(r.key) || null;
  const person = primaryPerson(c);
  const exhibit = c && c.exhibit;
  return {
    id: idx + 1,
    key: r.key,
    name: r.name || (c && c.name) || r.key,
    mono: monoOf(r.name || (c && c.name) || r.key),
    country: (r.country || (c && c.country) || "") + (r.type_label ? " · " + r.type_label : ""),
    cat: catOf(r.products || (c && c.products) || []),
    type: r.type_label || (c && c.type) || "",
    status: r.status || (c && c.status) || "У базі",
    score: r.score || 0,
    rating: ratingOf(r.score),
    us: r.outbound != null ? r.outbound : (c ? c.outb : 0),
    them: r.inbound != null ? r.inbound : (c ? c.inb : 0),
    threads: r.threads != null ? r.threads : (c ? c.threads : 0),
    quotes: r.quotes != null ? r.quotes : (c ? c.quotes : 0),
    manager: person ? (person.name || "") : "",
    email: person ? (person.email || "") : "",
    phone: person ? (person.phone || "") : "",
    site: r.key,
    cphi: exhibit ? ("CPHI Milan · Hall " + (exhibit.hall || "") + " · " + (exhibit.stand || "")).replace(/\s+·\s+$/, "") : "",
    products: r.products || (c && c.products) || [],
    positions: positionsFromCompany(c)
  };
});

const withPos = suppliers.filter(s => s.positions.length).length;
console.log("  suppliers built:", suppliers.length, "| with price history:", withPos);

// ---------- 3. write ----------
const siteOut = path.join(OUT, "data/site-data.js");
fs.mkdirSync(path.dirname(siteOut), { recursive: true });
fs.writeFileSync(siteOut, "window.GP_SITE = " + JSON.stringify({ companies, maxMsgs }) + ";\n", "utf8");
console.log("Wrote", siteOut, fs.statSync(siteOut).size, "bytes");

const supOut = path.join(OUT, "suppliers-data.js");
fs.writeFileSync(supOut, "window.GP_SUPPLIERS = " + JSON.stringify(suppliers) + ";\n", "utf8");
console.log("Wrote", supOut, fs.statSync(supOut).size, "bytes");

console.log("DONE.");
