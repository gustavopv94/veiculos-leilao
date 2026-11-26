// Versão estática (GitHub Pages): responde às chamadas /api/... no próprio navegador.
// Os dados vêm de dados/lista.json (+ dados/det/XX.json sob demanda); os favoritos ficam em favoritos.json no
// repositório do GitHub (gravados com um token pessoal guardado só neste navegador).
(() => {
"use strict";
const FACETAS = ["condicao", "uf", "tipo", "fonte", "marca", "origem", "combustivel", "cambio", "cidade"];
const ORDENS = {
  encerra: (a, b) => cmpNulo(a.data_ref || null, b.data_ref || null, 1),
  preco_asc: (a, b) => cmpNulo(a.preco_ref, b.preco_ref, 1),
  preco_desc: (a, b) => cmpNulo(a.preco_ref, b.preco_ref, -1),
  ano_desc: (a, b) => cmpNulo(a.ano_modelo, b.ano_modelo, -1),
  ano_asc: (a, b) => cmpNulo(a.ano_modelo, b.ano_modelo, 1),
  km_asc: (a, b) => cmpNulo(a.km, b.km, 1),
  desconto: (a, b) => cmpNulo(a.desconto, b.desconto, -1),
  recentes: (a, b) => cmpNulo(a.data_ref || null, b.data_ref || null, -1),
};
function cmpNulo(x, y, dir) {
  const nx = x == null || x === "", ny = y == null || y === "";
  if (nx || ny) return nx === ny ? 0 : nx ? 1 : -1;
  return (x < y ? -1 : x > y ? 1 : 0) * dir;
}
const norm = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const json = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Content-Type": "application/json" } });

let LOTES = null, POR_ID = null, META = null;
const carregar = (async () => {
  const [l, m] = await Promise.all([fetch("dados/lista.json").then(r => r.json()), fetch("dados/meta.json").then(r => r.json())]);
  META = m;
  LOTES = l.rows.map(r => { const o = {}; l.cols.forEach((c, i) => o[c] = r[i]); o.ativo = 1;
    o._q = norm([o.titulo, o.marca, o.modelo, o.cidade, o.uf, o.fonte].join(" ")); return o; });
  POR_ID = new Map(LOTES.map(o => [o.id, o]));
  await Favs.carregar();
})();

// ------------------------------------------------------------------ favoritos (GitHub)
// Toda alteração entra primeiro na fila "favs_pend" ({id: dados | null}, null = removido). A fila só é limpa
// depois que o GitHub aceita a gravação; ao abrir o site, o arquivo remoto é mesclado COM a fila por cima —
// assim nada salvo neste navegador se perde (antes o arquivo remoto antigo sobrescrevia tudo).
const lerLS = (k, def) => { try { return JSON.parse(localStorage.getItem(k)) ?? def; } catch (e) { return def; } };
const Favs = {
  dados: {}, sha: null,
  token() { return localStorage.getItem("gh_token") || ""; },
  repo() { return window.REPO || (META && META.repo) || ""; },
  pendentes() { return lerLS("favs_pend", {}); },
  temPendentes() { return Object.keys(this.pendentes()).length > 0; },
  alterar(id, valor) {  // valor = dados do favorito, ou null para remover
    if (valor) this.dados[id] = valor; else delete this.dados[id];
    const p = this.pendentes(); p[id] = valor || null;
    localStorage.setItem("favs_pend", JSON.stringify(p));
    localStorage.setItem("favs_cache", JSON.stringify(this.dados));
  },
  aplicarPendentes(base) {
    const out = { ...base };
    for (const [id, v] of Object.entries(this.pendentes())) { if (v) out[id] = v; else delete out[id]; }
    return out;
  },
  async lerRemoto() {  // devolve {dados, sha} ou null
    const repo = this.repo();
    if (!repo) return null;
    const h = { Accept: "application/vnd.github+json" };
    if (this.token()) h.Authorization = "Bearer " + this.token();
    const r = await fetch(`https://api.github.com/repos/${repo}/contents/favoritos.json?t=${Date.now()}`, { headers: h, cache: "no-store" });
    if (r.status === 404) return { dados: {}, sha: null };
    if (!r.ok) return null;
    const j = await r.json();
    return { dados: JSON.parse(decodeURIComponent(escape(atob(j.content.replace(/\n/g, ""))))) || {}, sha: j.sha };
  },
  async carregar() {
    this.dados = lerLS("favs_cache", {});
    try {
      const rem = await this.lerRemoto();
      if (rem) {
        this.sha = rem.sha;
        this.dados = this.aplicarPendentes(rem.dados);
        localStorage.setItem("favs_cache", JSON.stringify(this.dados));
      }
    } catch (e) { /* offline: usa o cache */ }
    if (this.token() && this.temPendentes()) await this.gravar("sincroniza favoritos").catch(() => {});
  },
  async gravar(msg) {
    localStorage.setItem("favs_cache", JSON.stringify(this.dados));
    const repo = this.repo(), tok = this.token();
    if (!repo || !tok) return { ok: false, motivo: "sem_token" };
    const url = `https://api.github.com/repos/${repo}/contents/favoritos.json`;
    const enviadas = this.pendentes();
    const corpo = () => JSON.stringify({ message: msg, sha: this.sha || undefined,
      content: btoa(unescape(encodeURIComponent(JSON.stringify(this.dados, null, 1)))) });
    const h = { Accept: "application/vnd.github+json", Authorization: "Bearer " + tok, "Content-Type": "application/json" };
    try {
      let r = await fetch(url, { method: "PUT", headers: h, body: corpo() });
      if (r.status === 409 || r.status === 422) {  // sha desatualizado: pega o atual, reaplica a fila e tenta de novo
        const rem = await this.lerRemoto();
        if (rem) { this.sha = rem.sha; this.dados = this.aplicarPendentes(rem.dados); }
        r = await fetch(url, { method: "PUT", headers: h, body: corpo() });
      }
      if (!r.ok) return { ok: false, motivo: `GitHub respondeu ${r.status}${r.status === 401 || r.status === 403 ? " (token inválido ou sem permissão)" : ""}` };
      this.sha = (await r.json()).content.sha;
    } catch (e) { return { ok: false, motivo: "sem conexão com o GitHub" }; }
    // limpa da fila só o que foi enviado (e não mudou no meio do caminho)
    const p = this.pendentes();
    for (const id of Object.keys(enviadas)) if (JSON.stringify(p[id]) === JSON.stringify(enviadas[id])) delete p[id];
    localStorage.setItem("favs_pend", JSON.stringify(p));
    localStorage.setItem("favs_cache", JSON.stringify(this.dados));
    return { ok: true };
  },
};
window.FavsGitHub = Favs;

function marcar(o) {
  const f = Favs.dados[o.id];
  return { ...o, fav: !!f, lembrar: !!(f && f.lembrar), antecedencia_min: f ? f.antecedencia_min : null, nota: f ? f.nota || "" : "" };
}
function favoritosComoLotes() {  // favoritos que saíram das fontes continuam visíveis
  return Object.entries(Favs.dados).filter(([id]) => !POR_ID.has(id)).map(([id, f]) => ({
    id, ativo: 0, titulo: f.titulo || id, link: f.link || "", foto: f.foto || "", fonte: f.fonte || "", condicao: f.condicao || "Não informado",
    uf: f.uf || "", cidade: f.cidade || "", preco_ref: f.preco_ref ?? null, data_ref: f.data_ref || "", data_encerramento: f.data_encerramento || "",
    _q: norm(f.titulo) }));
}

// ------------------------------------------------------------------ filtros
const lista = v => (v || "").split("|").map(x => x.trim()).filter(Boolean);
const num = v => (v === null || v === undefined || v === "" || isNaN(+v)) ? null : +v;
function filtro(p, ignorar = "") {
  const f = Object.fromEntries(p.entries());
  const multi = {};
  for (const k of FACETAS) if (k !== ignorar) { const v = lista(f[k]); if (v.length) multi[k] = new Set(v.map(x => x === "(vazio)" ? "" : x)); }
  const termos = norm(f.q || "").match(/[a-z0-9]+/g) || [];
  const modelo = norm(f.modelo || "");
  const anoMin = num(f.ano_min), anoMax = num(f.ano_max), kmMin = num(f.km_min), kmMax = num(f.km_max);
  const pMin = num(f.preco_min), pMax = num(f.preco_max), dias = num(f.dias);
  const semKm = !["0", "false", ""].includes(f.sem_km ?? "1");
  const agora = new Date().toISOString().slice(0, 19), lim = dias != null ? new Date(Date.now() + dias * 864e5).toISOString().slice(0, 19) : null;
  const soFav = f.fav === "1", soLemb = f.lembrar === "1", soFoto = f.so_foto === "1", comKm = f.com_km === "1";
  return o => {
    for (const k in multi) if (!multi[k].has(o[k] ?? "")) return false;
    if (soFav && !Favs.dados[o.id]) return false;
    if (soLemb && !(Favs.dados[o.id] && Favs.dados[o.id].lembrar)) return false;
    for (const t of termos) if (!o._q.includes(t)) return false;
    if (modelo && !norm(o.modelo).includes(modelo)) return false;
    const ano = o.ano_modelo ?? o.ano_fab;
    if (anoMin != null && !(ano != null && ano >= anoMin)) return false;
    if (anoMax != null && !(ano != null && ano <= anoMax)) return false;
    if (kmMin != null && !(o.km == null ? semKm : o.km >= kmMin)) return false;
    if (kmMax != null && !(o.km == null ? semKm : o.km <= kmMax)) return false;
    if (comKm && o.km == null) return false;
    if (pMin != null && !(o.preco_ref != null && o.preco_ref >= pMin)) return false;
    if (pMax != null && !(o.preco_ref != null && o.preco_ref <= pMax)) return false;
    if (lim && !(o.data_ref && o.data_ref >= agora && o.data_ref <= lim)) return false;
    if (soFoto && !o.foto) return false;
    return true;
  };
}
function base(p) { return p.get("fav") === "1" ? LOTES.concat(favoritosComoLotes()) : LOTES; }
function buscar(p) {
  const arr = base(p).filter(filtro(p)).sort((ORDENS[p.get("ordem")] || ORDENS.encerra));
  return arr;
}
function facetas(p) {
  const out = {};
  for (const k of FACETAS) {
    if (k === "cidade" && !lista(p.get("uf")).length) continue;
    const cont = new Map();
    for (const o of base(p).filter(filtro(p, k))) { const v = o[k] || "(vazio)"; cont.set(v, (cont.get(v) || 0) + 1); }
    out[k] = [...cont].map(([v, n]) => ({ v, n })).sort((a, b) => b.n - a.n).slice(0, k === "marca" || k === "cidade" ? 400 : 100);
  }
  const sel = base(p).filter(filtro(p));
  const anos = sel.map(o => o.ano_modelo ?? o.ano_fab).filter(v => v != null);
  out.faixas = { a0: anos.length ? Math.min(...anos) : null, a1: anos.length ? Math.max(...anos) : null };
  return out;
}
function djb2(s) { let h = 5381; for (const b of new TextEncoder().encode(s)) h = (Math.imul(h, 33) ^ b) >>> 0; return (h & 255).toString(16).padStart(2, "0"); }
const pedacos = {};
async function detalhe(id) {
  const o = POR_ID.get(id) || favoritosComoLotes().find(x => x.id === id);
  if (!o) return null;
  const p = djb2(id);
  if (o.ativo !== 0) pedacos[p] = pedacos[p] || fetch(`dados/det/${p}.json`).then(r => r.json());
  const det = o.ativo !== 0 ? ((await pedacos[p])[id] || {}) : {};
  return marcar({ ...o, ...det });
}

// ------------------------------------------------------------------ CSV
window.baixarCSV = async (qs) => {
  await carregar;
  const arr = buscar(new URLSearchParams(qs));
  const cols = ["titulo", "marca", "modelo", "ano_fab", "ano_modelo", "km", "condicao", "tipo", "origem", "uf", "cidade",
    "preco_ref", "valor_avaliacao", "lance_atual", "data_encerramento", "status", "fonte", "link"];
  const esc = v => { v = v == null ? "" : String(v); if (typeof v === "string" && /^\d+\.\d+$/.test(v)) v = v.replace(".", ","); return /[;"\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; };
  const txt = "﻿" + [cols.join(";"), ...arr.map(o => cols.map(c => esc(o[c])).join(";"))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([txt], { type: "text/csv;charset=utf-8" }));
  a.download = "veiculos_leilao.csv"; a.click();
};

// ------------------------------------------------------------------ interceptação
const fetchOriginal = window.fetch.bind(window);
window.fetch = async (input, init = {}) => {
  const url = typeof input === "string" ? input : input.url;
  if (!url.startsWith("/api/")) return fetchOriginal(input, init);
  await carregar;
  const u = new URL(url, location.href), p = u.searchParams, metodo = (init.method || "GET").toUpperCase();
  const rota = u.pathname;
  if (rota === "/api/lotes") {
    const arr = buscar(p), pag = Math.max(1, +p.get("pagina") || 1), pp = Math.min(500, +p.get("por_pagina") || 48);
    return json({ total: arr.length, pagina: pag, por_pagina: pp, itens: arr.slice((pag - 1) * pp, pag * pp).map(marcar) });
  }
  if (rota === "/api/facetas") return json(facetas(p));
  if (rota.startsWith("/api/lotes/")) {
    const d = await detalhe(decodeURIComponent(rota.slice(11)));
    return d ? json(d) : json({ detail: "lote não encontrado" }, 404);
  }
  if (rota === "/api/status") return json({ ultima_coleta: META.gerado_em, ativos: META.ativos, fontes: META.fontes });
  if (rota === "/api/favoritos") return json(Object.entries(Favs.dados).map(([id, f]) => ({ id, ...f })));
  if (rota.startsWith("/api/favoritos/")) {
    const id = decodeURIComponent(rota.slice(15));
    if (metodo === "DELETE") {
      Favs.alterar(id, null);
      const r = await Favs.gravar("remove favorito");
      return json({ ok: true, ...r });
    }
    const dados = init.body ? JSON.parse(init.body) : {};
    const o = POR_ID.get(id) || {}, antigo = Favs.dados[id] || {};
    Favs.alterar(id, { criado_em: antigo.criado_em || new Date().toISOString().slice(0, 19), lembrar: !!dados.lembrar,
      antecedencia_min: Math.max(60, +dados.antecedencia_min || 60), nota: String(dados.nota || "").slice(0, 500),
      titulo: o.titulo || antigo.titulo, link: o.link || antigo.link, foto: o.foto || antigo.foto, fonte: o.fonte || antigo.fonte,
      condicao: o.condicao || antigo.condicao, uf: o.uf || antigo.uf, cidade: o.cidade || antigo.cidade,
      preco_ref: o.preco_ref ?? antigo.preco_ref ?? null, data_encerramento: o.data_encerramento || antigo.data_encerramento || "",
      data_ref: o.data_ref || antigo.data_ref || "" });
    const r = await Favs.gravar("atualiza favorito");
    return json({ id, ...Favs.dados[id], ...r });
  }
  if (rota === "/api/lembretes/config") {
    return json({ estatico: true, token: !!Favs.token(), repo: Favs.repo(), algum_celular: !!Favs.token(),
      pendentes: Favs.temPendentes() });
  }
  return json({ detail: "não disponível na versão online" }, 404);
};
})();
