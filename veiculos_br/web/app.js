(() => {
"use strict";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const brl = v => v == null ? "" : v.toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const num = v => v == null ? "" : Number(v).toLocaleString("pt-BR");

const MULTI = ["condicao", "uf", "tipo", "origem", "combustivel", "cambio", "fonte"];  // vários valores, separados por |
const SIMPLES = ["q", "cidade", "marca", "modelo", "preco_min", "preco_max", "ano_min", "ano_max", "km_min", "km_max", "dias", "ordem"];
const BOOL = { sem_km: "1", so_foto: "", fav: "" };  // valor padrão
const ORDEM_COND = ["Sucata", "Grande monta", "Média monta", "Pequena monta", "Sinistrado", "Conservado", "Não informado"];
const CLASSE_COND = { "Sucata": "sucata", "Grande monta": "grande", "Média monta": "media", "Pequena monta": "pequena",
  "Sinistrado": "sinistrado", "Conservado": "conservado", "Não informado": "ni" };
const ROTULO_COND = { "Sinistrado": "Sinistrado (sem classif.)" };
const POR_PAGINA = 48;

let estado = {};
let pagina = 1, total = 0, carregando = false, geracao = 0;

// ---------------------------------------------------------------- estado <-> URL
function lerURL() {
  const p = new URLSearchParams(location.search);
  estado = {};
  for (const k of MULTI) estado[k] = (p.get(k) || "").split("|").filter(Boolean);
  for (const k of SIMPLES) estado[k] = p.get(k) || "";
  for (const [k, def] of Object.entries(BOOL)) estado[k] = p.has(k) ? p.get(k) : def;
  if (!estado.ordem) estado.ordem = "encerra";
}
function params(extra = {}) {
  const p = new URLSearchParams();
  for (const k of MULTI) if (estado[k].length) p.set(k, estado[k].join("|"));
  for (const k of SIMPLES) if (estado[k] && !(k === "ordem" && estado[k] === "encerra")) p.set(k, estado[k]);
  for (const [k, def] of Object.entries(BOOL)) if (estado[k] !== def) p.set(k, estado[k]);
  for (const [k, v] of Object.entries(extra)) p.set(k, v);
  return p;
}
function gravarURL() {
  const qs = params().toString();
  history.replaceState(null, "", qs ? "?" + qs : location.pathname);
}

// ---------------------------------------------------------------- controles
function sincronizarControles() {
  for (const k of ["q", "modelo", "preco_min", "preco_max", "ano_min", "ano_max", "km_min", "km_max", "dias", "ordem"]) $("#" + k).value = estado[k];
  $("#sem_km").checked = estado.sem_km === "1";
  $("#so_foto").checked = estado.so_foto === "1";
}

let tDigita;
function ligar() {
  const muda = () => { gravarURL(); recarregar(); };
  const texto = (id) => $("#" + id).addEventListener("input", e => {
    clearTimeout(tDigita);
    tDigita = setTimeout(() => { estado[id] = e.target.value.trim(); muda(); }, 350);
  });
  ["q", "modelo", "preco_min", "preco_max", "ano_min", "ano_max", "km_min", "km_max"].forEach(texto);
  $("#dias").addEventListener("change", e => { estado.dias = e.target.value; muda(); });
  $("#ordem").addEventListener("change", e => { estado.ordem = e.target.value; muda(); });
  $("#sem_km").addEventListener("change", e => { estado.sem_km = e.target.checked ? "1" : "0"; muda(); });
  $("#so_foto").addEventListener("change", e => { estado.so_foto = e.target.checked ? "1" : ""; muda(); });
  $("#f-uf").addEventListener("change", e => {
    estado.uf = [...e.target.selectedOptions].map(o => o.value).filter(Boolean);
    estado.cidade = "";
    muda();
  });
  $("#f-cidade").addEventListener("change", e => { estado.cidade = e.target.value; muda(); });
  $("#f-marca").addEventListener("change", e => { estado.marca = e.target.value; muda(); });
  $("#limpar").addEventListener("click", () => { history.replaceState(null, "", location.pathname); lerURL(); sincronizarControles(); recarregar(); });
  $("#abrirFiltros").addEventListener("click", () => $("#filtros").classList.add("aberto"));
  $("#fecharFiltros").addEventListener("click", () => $("#filtros").classList.remove("aberto"));
  $("#m-fechar").addEventListener("click", () => { $("#modal").close(); });
  $("#modal").addEventListener("close", () => { if (estado.fav === "1") recarregar(); });
  $("#btnFav").addEventListener("click", () => { estado.fav = estado.fav === "1" ? "" : "1"; muda(); });
  $("#btnCfg").addEventListener("click", abrirConfig);
  $("#cfg-fechar").addEventListener("click", () => $("#cfg").close());
  $("#cfg-salvar").addEventListener("click", () => { $("#cfg-msg").textContent = "salvando e testando o envio…"; salvarConfig(); });
  $("#cfg-teste").addEventListener("click", async () => {
    $("#cfg-msg").textContent = "enviando…";
    const r = await fetch("/api/lembretes/teste", { method: "POST" });
    const d = await r.json();
    $("#cfg-msg").textContent = r.ok ? "✔ enviado por: " + d.canais.join(", ") : "✘ " + (d.detail || "falhou");
  });
  $("#modal").addEventListener("click", e => { if (e.target === $("#modal")) $("#modal").close(); });
  $("#csv").addEventListener("click", e => { e.preventDefault();
    if (window.baixarCSV) window.baixarCSV(params().toString()); else location.href = "/api/csv?" + params().toString(); });
  document.addEventListener("click", e => {
    const chip = e.target.closest("[data-f]");
    if (chip && chip.closest(".filtros")) {
      const f = chip.dataset.f, v = chip.dataset.v;
      const arr = estado[f];
      const i = arr.indexOf(v);
      i >= 0 ? arr.splice(i, 1) : arr.push(v);
      muda();
      return;
    }
    const rm = e.target.closest("[data-rm]");
    if (rm) {
      const [f, v] = [rm.dataset.rm, rm.dataset.v];
      if (MULTI.includes(f)) estado[f] = estado[f].filter(x => x !== v);
      else estado[f] = BOOL[f] ?? "";
      sincronizarControles();
      muda();
      return;
    }
    const fb = e.target.closest("[data-fav]");
    if (fb) {
      const on = !fb.classList.contains("on");
      favoritar(fb.dataset.fav, on).then(() => {
        fb.classList.toggle("on", on); fb.textContent = on ? "♥" : "♡";
        if (!on) fb.parentNode.querySelector(".sino")?.remove();
        if (!on && estado.fav === "1") recarregar();
      });
      return;
    }
    const card = e.target.closest(".card");
    if (card && !e.target.closest("a")) abrirLote(card.dataset.id);
  });
  new IntersectionObserver(es => { if (es[0].isIntersecting) carregarMais(); }, { rootMargin: "800px" }).observe($("#fim"));
}

// ---------------------------------------------------------------- facetas
function chips(id, campo, lista, ordem) {
  const sel = estado[campo];
  let itens = lista.slice();
  for (const v of sel) if (!itens.some(x => x.v === v)) itens.push({ v, n: 0 });
  if (ordem) itens.sort((a, b) => (ordem.indexOf(a.v) + 99) % 99 - (ordem.indexOf(b.v) + 99) % 99);
  $(id).innerHTML = itens.map(x => {
    const rot = (campo === "condicao" ? (ROTULO_COND[x.v] || x.v) : x.v);
    const cls = campo === "condicao" ? ` cond ${CLASSE_COND[x.v] || ""}` : "";
    return `<button class="chip${sel.includes(x.v) ? " on" : ""}${x.n ? "" : " zero"}" data-f="${campo}" data-v="${esc(x.v)}">` +
      `${campo === "condicao" ? `<span class="${cls.trim()}" style="padding:0 4px">${esc(rot)}</span>` : esc(rot)}<span class="n">${num(x.n)}</span></button>`;
  }).join("");
}
function opcoes(id, lista, atual, rotulo, multi = false) {
  const sel = multi ? atual : [atual];
  let itens = lista.slice();
  for (const v of sel) if (v && !itens.some(x => x.v === v)) itens.unshift({ v, n: 0 });
  $(id).innerHTML = (multi ? "" : `<option value="">${rotulo}</option>`) +
    itens.map(x => `<option value="${esc(x.v)}"${sel.includes(x.v) ? " selected" : ""}>${esc(x.v)} (${num(x.n)})</option>`).join("");
}
async function carregarFacetas(g) {
  const r = await fetch("/api/facetas?" + params().toString());
  const f = await r.json();
  if (g !== geracao) return;
  chips("#f-condicao", "condicao", f.condicao, ORDEM_COND);
  chips("#f-tipo", "tipo", f.tipo);
  chips("#f-origem", "origem", f.origem);
  chips("#f-combustivel", "combustivel", f.combustivel.filter(x => x.v !== "(vazio)"));
  chips("#f-cambio", "cambio", f.cambio.filter(x => x.v !== "(vazio)"));
  chips("#f-fonte", "fonte", f.fonte);
  const ufs = f.uf.filter(x => x.v !== "(vazio)").sort((a, b) => a.v.localeCompare(b.v));
  opcoes("#f-uf", ufs, estado.uf, "", true);
  $("#f-cidade").hidden = !estado.uf.length;
  if (f.cidade) opcoes("#f-cidade", f.cidade.filter(x => x.v !== "(vazio)").sort((a, b) => a.v.localeCompare(b.v, "pt-BR")), estado.cidade, "Todas as cidades");
  opcoes("#f-marca", f.marca.filter(x => x.v !== "(vazio)").sort((a, b) => a.v.localeCompare(b.v)), estado.marca, "Todas as marcas");
}

// ---------------------------------------------------------------- lista
function filtrosAtivos() {
  const out = [];
  for (const k of MULTI) for (const v of estado[k]) out.push([k, v, v]);
  const rot = { q: "busca", cidade: "", marca: "", modelo: "modelo", preco_min: "preço ≥", preco_max: "preço ≤", ano_min: "ano ≥",
    ano_max: "ano ≤", km_min: "km ≥", km_max: "km ≤", dias: "encerra em ≤ dias:" };
  for (const [k, r] of Object.entries(rot)) if (estado[k]) out.push([k, "", `${r} ${estado[k]}`.trim()]);
  if (estado.fav === "1") out.push(["fav", "", "♥ só favoritos"]);
  if (estado.so_foto === "1") out.push(["so_foto", "", "só com foto"]);
  if (estado.sem_km === "0") out.push(["sem_km", "", "só com km informado"]);
  $("#ativos").innerHTML = out.map(([f, v, t]) => `<button class="chip" data-rm="${f}" data-v="${esc(v)}">${esc(t)} ✕</button>`).join("");
}
function badge(c) { return `<span class="cond ${CLASSE_COND[c] || "ni"}">${esc(ROTULO_COND[c] || c || "Não informado")}</span>`; }
function cartao(l) {
  const ano = l.ano_modelo ? (l.ano_fab && l.ano_fab !== l.ano_modelo ? `${l.ano_fab}/${l.ano_modelo}` : l.ano_modelo) : "";
  const specs = [ano && `📅 ${ano}`, l.km != null && `🛣️ ${num(l.km)} km`, l.combustivel, l.cambio].filter(Boolean);
  const loc = [l.cidade, l.uf].filter(Boolean).join("/");
  const precoRot = l.lance_atual ? "lance atual" : l.preco_ref ? "lance inicial / mínimo" : "";
  const quando = l.data_encerramento ? `⏱ ${esc(l.data_encerramento)}` : esc(l.status || "");
  return `<article class="card${l.ativo === 0 ? " inativo" : ""}" data-id="${esc(l.id)}">
    <div class="foto"><button class="fav-btn${l.fav ? " on" : ""}" data-fav="${esc(l.id)}" title="${l.fav ? "Remover dos favoritos" : "Salvar nos favoritos"}">${l.fav ? "♥" : "♡"}</button>
      ${l.lembrar ? `<span class="sino" title="Lembrete ${esc(rotuloAnt(l.antecedencia_min))} antes">🔔</span>` : ""}${l.foto ? `<img loading="lazy" src="${esc(l.foto)}" alt="" onerror="this.remove()">` : ""}<div class="sem">sem foto</div>
      <div class="cond-wrap" style="position:absolute;left:8px;top:8px">${badge(l.condicao)}</div></div>
    <div class="corpo">
      <div class="tit" title="${esc(l.titulo)}">${esc(l.titulo)}</div>
      <div class="specs">${specs.map(esc).join("<span>·</span>")}</div>
      <div class="preco">${l.preco_ref ? brl(l.preco_ref) : "<span style='color:var(--mut);font-size:14px'>preço não informado</span>"}
        <small>${precoRot}${l.valor_avaliacao && l.preco_ref ? ` · avaliação ${brl(l.valor_avaliacao)}` : ""}</small></div>
      <div class="rodape"><span>📍 ${esc(loc || "—")}</span><span>${quando}</span></div>
      <div class="rodape" style="border:0;padding:0"><span>${esc(l.fonte)}${l.ativo === 0 ? " · saiu do site" : ""}</span><span>${esc(l.tipo || "")}</span></div>
    </div></article>`;
}
async function carregarMais() {
  if (carregando || (pagina > 1 && (pagina - 1) * POR_PAGINA >= total)) return;
  carregando = true;
  const g = geracao;
  $("#fim").textContent = "carregando…";
  try {
    const r = await fetch("/api/lotes?" + params({ pagina, por_pagina: POR_PAGINA }).toString());
    const d = await r.json();
    if (g !== geracao) return;
    total = d.total;
    if (pagina === 1) $("#grade").innerHTML = d.itens.length ? "" : `<div class="vazio">Nenhum veículo com esses filtros.</div>`;
    $("#grade").insertAdjacentHTML("beforeend", d.itens.map(cartao).join(""));
    $("#resumo").innerHTML = `<b>${num(total)}</b> veículos`;
    pagina++;
    $("#fim").textContent = (pagina - 1) * POR_PAGINA >= total ? (total ? "fim da lista" : "") : "";
  } catch (e) {
    $("#fim").textContent = "erro ao carregar: " + e;
  } finally {
    if (g === geracao) carregando = false;
  }
}
function recarregar() {
  $("#btnFav").classList.toggle("on", estado.fav === "1");
  contarFav();
  geracao++;
  pagina = 1; total = 0; carregando = false;
  filtrosAtivos();
  carregarFacetas(geracao);
  carregarMais();
}

// ---------------------------------------------------------------- detalhe
async function abrirLote(id) {
  const l = await (await fetch("/api/lotes/" + encodeURIComponent(id))).json();
  $("#m-titulo").textContent = l.titulo;
  const linha = (k, v) => v || v === 0 ? `<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>` : "";
  const ano = [l.ano_fab, l.ano_modelo].filter(Boolean).join("/");
  const principal = [
    ["Condição", l.condicao], ["Marca", l.marca], ["Modelo", l.modelo], ["Ano fab./modelo", ano],
    ["Quilometragem", l.km != null ? num(l.km) + " km" : "não informada"], ["Combustível", l.combustivel], ["Câmbio", l.cambio],
    ["Cor", l.cor], ["Final da placa", l.final_placa], ["Tipo", l.tipo], ["Origem", l.origem],
    ["Local", [l.cidade, l.uf].filter(Boolean).join("/")], ["Endereço", l.endereco],
  ];
  const leilao = [
    ["Preço de referência", brl(l.preco_ref)], ["Lance atual", brl(l.lance_atual)], ["Lance inicial / mínimo", brl(l.valor_minimo)],
    ["Avaliação / valor de mercado", brl(l.valor_avaliacao)], ["Desconto", l.desconto ? l.desconto + "%" : ""],
    ["Data do leilão", l.data_encerramento], ["Situação", l.status], ["Modalidade", l.modalidade_original || l.modalidade],
    ["Comitente", l.comitente], ["Leiloeiro", l.leiloeiro], ["Fonte", l.fonte],
  ];
  const pracas = (l.pracas || []).map(p => linha(p.nome, [p.data, p.valor ? brl(p.valor) : ""].filter(Boolean).join(" — "))).join("");
  const det = Object.entries(l.detalhes || {}).map(([k, v]) => linha(k, v)).join("");
  const fotos = (l.fotos || []).map(u => `<a href="${esc(u)}" target="_blank" rel="noopener"><img loading="lazy" src="${esc(u)}" alt="" onerror="this.parentNode.remove()"></a>`).join("");
  const docs = (l.documentos || []).map(d => `<a class="btn" href="${esc(d.url)}" target="_blank" rel="noopener">📄 ${esc(d.nome)}</a>`).join("");
  $("#m-corpo").innerHTML = `
    ${fotos ? `<div class="galeria">${fotos}</div>` : ""}
    <div class="m-acoes">${badge(l.condicao)}
      <a class="btn prim" href="${esc(l.link)}" target="_blank" rel="noopener">Abrir no site do leilão ↗</a>${docs}</div>
    <div class="lembrete-box" id="lb">
      <button class="btn" id="lb-fav">${l.fav ? "♥ Nos favoritos" : "♡ Salvar nos favoritos"}</button>
      <label>🔔 Lembrar
        <select id="lb-ant">
          <option value="0">não lembrar</option>
          ${ANTECEDENCIAS.map(m => `<option value="${m}"${l.lembrar && l.antecedencia_min === m ? " selected" : ""}>${rotuloAnt(m)} antes</option>`).join("")}
        </select></label>
      <input id="lb-nota" type="text" maxlength="500" placeholder="anotação (ex.: lance máx. R$ 20 mil)" value="${esc(l.nota || "")}">
      <span id="lb-msg" class="aviso">${l.data_ref ? "" : "sem data de leilão ainda — o lembrete vale quando a data aparecer numa próxima coleta"}</span>
    </div>
    <div class="m-grid">
      <div><h3>Veículo</h3><table>${principal.map(([k, v]) => linha(k, v)).join("")}</table></div>
      <div><h3>Leilão</h3><table>${leilao.map(([k, v]) => linha(k, v)).join("")}${pracas}</table></div>
    </div>
    ${det ? `<h3>Todos os detalhes da fonte</h3><div class="m-grid"><div style="grid-column:1/-1"><table>${det}</table></div></div>` : ""}
    ${l.descricao ? `<h3>Descrição</h3><div class="m-desc">${esc(l.descricao)}</div>` : ""}
    <p class="aviso">Dados copiados de ${esc(l.fonte)} em ${esc((l.visto_em || "").replace("T", " "))}. Confira sempre o edital e a página oficial antes de dar lance.</p>`;
  $("#modal").showModal();
  $("#modal").scrollTop = 0;
  let fav = !!l.fav;
  const salvar = async (msg) => {
    const ant = +$("#lb-ant").value;
    if (ant && !fav) fav = true;  // lembrete implica favorito
    const res = await favoritar(l.id, fav, { lembrar: ant > 0, antecedencia_min: ant || 60, nota: $("#lb-nota").value });
    $("#lb-fav").textContent = fav ? "♥ Nos favoritos" : "♡ Salvar nos favoritos";
    if (!fav) $("#lb-ant").value = "0";
    $("#lb-msg").textContent = msg || (fav ? (ant ? `✔ lembrete ${rotuloAnt(ant)} antes do leilão` : "✔ salvo nos favoritos") : "removido dos favoritos");
    const c = document.querySelector(`.card[data-id="${CSS.escape(l.id)}"] .fav-btn`);
    if (c) { c.classList.toggle("on", fav); c.textContent = fav ? "♥" : "♡"; }
    const cfg = await (await fetch("/api/lembretes/config")).json();
    if (res && res.ok === false && res.motivo !== "sem_token") $("#lb-msg").textContent = "✘ não salvou no GitHub: " + res.motivo;
    else if (cfg.estatico && !cfg.token) $("#lb-msg").textContent += " · salvo só neste navegador — configure o token no 🔔 do topo para sincronizar e receber lembretes";
    else if (ant && !cfg.algum_celular) $("#lb-msg").textContent += " · configure o e-mail no 🔔 do topo";
  };
  $("#lb-fav").addEventListener("click", () => { fav = !fav; salvar(); });
  $("#lb-ant").addEventListener("change", () => salvar());
  let tNota;
  $("#lb-nota").addEventListener("input", () => { clearTimeout(tNota); tNota = setTimeout(() => { if (fav) salvar("✔ anotação salva"); }, 600); });
}

// ---------------------------------------------------------------- favoritos / lembretes
const ANTECEDENCIAS = window.MODO_ESTATICO ? [60, 180, 720, 1440, 2880] : [30, 60, 180, 720, 1440, 2880];
function rotuloAnt(m) { return !m ? "" : m < 60 ? `${m} min` : m < 1440 ? `${m / 60} h` : `${m / 1440} dia${m >= 2880 ? "s" : ""}`; }
async function favoritar(id, on, extra = {}) {
  const url = "/api/favoritos/" + encodeURIComponent(id);
  const r = on ? await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(extra) })
    : await fetch(url, { method: "DELETE" });
  contarFav();
  try { return await r.json(); } catch (e) { return null; }
}
async function contarFav() {
  try { const f = await (await fetch("/api/favoritos")).json(); $("#nFav").textContent = f.length ? `(${f.length})` : ""; } catch (e) { /* */ }
}
function configEstatico(c) {
  $("#cfg .cfg-corpo").innerHTML = `
    <p>Os favoritos ficam salvos no arquivo <code>favoritos.json</code> do repositório <b>${esc(c.repo)}</b>. Para gravar nele
      (e o GitHub enviar os lembretes por e-mail), este navegador precisa de um <b>token</b> do GitHub — feito uma vez em cada aparelho.</p>
    <ol class="aviso">
      <li>Abra <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens/new</a>.</li>
      <li>Nome: “leiloes”; validade: a que preferir; <b>Repository access</b>: <i>Only select repositories</i> → <b>${esc(c.repo.split("/")[1] || "")}</b>.</li>
      <li><b>Permissions → Repository permissions → Contents: Read and write</b>. Gere e copie o token.</li>
    </ol>
    <input id="cfg-token" type="password" placeholder="${c.token ? "token já salvo neste navegador — cole outro para trocar" : "github_pat_..."}" style="width:100%">
    <div class="m-acoes">
      <button id="cfg-salvar2" class="btn prim">Salvar token</button>
      ${c.token ? `<button id="cfg-sair" class="btn">Esquecer token</button>` : ""}
      <span id="cfg-msg" class="aviso">${c.token ? "Token configurado ✔" : ""}</span>
    </div>
    <p class="aviso">Lembretes: o GitHub confere de hora em hora e manda e-mail para o endereço configurado no repositório.
      O token fica guardado só neste navegador.</p>`;
  $("#cfg-salvar2").addEventListener("click", async () => {
    const t = $("#cfg-token").value.trim();
    if (!t) return;
    $("#cfg-msg").textContent = "conferindo…";
    const r = await fetch(`https://api.github.com/repos/${c.repo}`, { headers: { Authorization: "Bearer " + t } });
    const j = r.ok ? await r.json() : {};
    if (!r.ok || !(j.permissions && j.permissions.push)) { $("#cfg-msg").textContent = "✘ token sem permissão de escrita nesse repositório"; return; }
    localStorage.setItem("gh_token", t);
    const locais = window.FavsGitHub.dados;
    await window.FavsGitHub.carregar();
    window.FavsGitHub.dados = { ...window.FavsGitHub.dados, ...locais };
    await window.FavsGitHub.gravar("sincroniza favoritos");
    $("#cfg-msg").textContent = "✔ token salvo — favoritos sincronizados com o GitHub";
    recarregar();
  });
  $("#cfg-sair")?.addEventListener("click", () => { localStorage.removeItem("gh_token"); $("#cfg-msg").textContent = "token removido deste navegador"; });
}
async function abrirConfig() {
  if (window.MODO_ESTATICO) { configEstatico(await (await fetch("/api/lembretes/config")).json()); $("#cfg").showModal(); return; }
  const c = await (await fetch("/api/lembretes/config")).json();
  $("#cfg-user").value = c.email_usuario || "";
  $("#cfg-para").value = c.email_para && c.email_para !== c.email_usuario ? c.email_para : "";
  $("#cfg-pass").value = "";
  $("#cfg-pass").placeholder = c.email ? "senha de app já salva — preencha só para trocar" : "senha de app (16 caracteres)";
  $("#cfg-win").checked = c.windows;
  $("#cfg-msg").textContent = c.email ? `E-mail configurado ✔ (envia para ${c.email_para})` : "";
  $("#cfg").showModal();
}
async function salvarConfig() {
  const r = await fetch("/api/lembretes/config", { method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email_usuario: $("#cfg-user").value, email_senha: $("#cfg-pass").value,
      email_para: $("#cfg-para").value, windows: $("#cfg-win").checked }) });
  const c = await r.json();
  if (!r.ok) { $("#cfg-msg").textContent = "✘ " + (c.detail || "erro ao salvar"); return; }
  $("#cfg-pass").value = "";
  $("#cfg-msg").textContent = c.email ? `✔ salvo — um e-mail de teste foi enviado para ${c.email_para}` : "✔ salvo";
}

// ---------------------------------------------------------------- início
async function status() {
  try {
    const s = await (await fetch("/api/status")).json();
    const erros = s.fontes.filter(f => !f.ok).map(f => f.fonte);
    $("#sub").textContent = s.ultima_coleta
      ? `${num(s.ativos)} veículos · atualizado em ${new Date(s.ultima_coleta).toLocaleString("pt-BR")}` + (erros.length ? ` · falhou: ${erros.join(", ")}` : "")
      : "banco vazio — rode: python -m veiculos_br coletar";
    if (s.link_publico && ["127.0.0.1", "localhost"].includes(location.hostname))
      $("#sub").insertAdjacentHTML("beforeend", ` · 🌐 <a href="${esc(s.link_publico)}" target="_blank" rel="noopener">${esc(s.link_publico.replace("https://", ""))}</a>`);
    $("#sub").title = s.fontes.map(f => `${f.fonte}: ${f.ok ? f.n + " lotes" : "ERRO " + f.erro}`).join("\n");
  } catch (e) { /* ignora */ }
}
lerURL();
sincronizarControles();
ligar();
status();
recarregar();
})();
