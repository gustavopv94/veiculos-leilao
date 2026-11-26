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
      favoritar(fb.dataset.fav, on).then((res) => {
        if (on && res && res.motivo === "sem_token" && !sessionStorage.getItem("aviso_token")) {
          sessionStorage.setItem("aviso_token", "1");
          toast(`♥ Salvo só neste navegador. <a href="#" id="t-cfg">Configure o token do GitHub</a> para sincronizar e receber lembretes por e-mail.`);
          $("#t-cfg")?.addEventListener("click", ev => { ev.preventDefault(); $("#toast").hidden = true; abrirConfig(); });
        } else if (res && res.ok === false && res.motivo !== "sem_token") toast("✘ Não salvou no GitHub: " + esc(res.motivo) + " (ficou salvo neste navegador)");
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
      <button class="btn" id="lb-calc">🧮 Calcular lance máximo</button>
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
    else if (cfg.estatico && !cfg.token) $("#lb-msg").insertAdjacentHTML("beforeend", ` · salvo só neste navegador${ant ? " — o lembrete NÃO será enviado" : ""}. <a href="#" id="lb-cfg">Configurar o token</a> para sincronizar e receber lembretes`);
    else if (cfg.estatico && ant && ant < 180) $("#lb-msg").textContent += " · o GitHub pode atrasar até ~1 h; prefira 3 h ou mais";
    else if (cfg.estatico && ant) $("#lb-msg").textContent += " · um e-mail de confirmação chega em alguns minutos";
    else if (ant && !cfg.algum_celular) $("#lb-msg").textContent += " · configure o e-mail no 🔔 do topo";
    $("#lb-cfg")?.addEventListener("click", e => { e.preventDefault(); abrirConfig(); });
  };
  $("#lb-fav").addEventListener("click", () => { fav = !fav; salvar(); });
  $("#lb-calc").addEventListener("click", () => abrirCalc({
    titulo: l.titulo, lance: l.lance_atual || l.preco_ref || l.valor_minimo, tipo: l.tipo, marca: l.marca, modelo: l.modelo,
    ano: l.ano_modelo || l.ano_fab,
    salvarNota: async (txt) => {
      const atual = $("#lb-nota").value.split(" | ").filter(p => p && !p.startsWith("Lance máx.")).join(" | ");
      $("#lb-nota").value = (txt + (atual ? " | " + atual : "")).slice(0, 500);
      fav = true;
      await salvar("✔ lance máximo salvo na anotação");
    } }));
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
  try {
    const f = await (await fetch("/api/favoritos")).json();
    const soLocal = window.FavsGitHub && window.FavsGitHub.temPendentes();
    $("#nFav").textContent = f.length ? `(${f.length}${soLocal ? " · só neste navegador" : ""})` : "";
    $("#nFav").title = soLocal ? "Ainda não enviados ao GitHub: configure o token no 🔔 do topo" : "";
  } catch (e) { /* */ }
}
function configEstatico(c) {
  $("#cfg .cfg-corpo").innerHTML = `
    <p>Os favoritos ficam salvos no arquivo <code>favoritos.json</code> do repositório <b>${esc(c.repo)}</b>. Para gravar nele
      (e o GitHub enviar os lembretes por e-mail), este navegador precisa de um <b>token</b> do GitHub — feito uma vez em cada aparelho.
      Sem o token, os favoritos ficam salvos só neste navegador e os lembretes não são enviados.</p>
    <ol class="aviso">
      <li>Abra <a href="https://github.com/settings/personal-access-tokens/new" target="_blank" rel="noopener">github.com/settings/personal-access-tokens/new</a>.</li>
      <li>Nome: “leiloes”; validade: a que preferir; <b>Repository access</b>: <i>Only select repositories</i> → <b>${esc(c.repo.split("/")[1] || "")}</b>.</li>
      <li><b>Permissions → Repository permissions</b>: <b>Contents: Read and write</b> e <b>Actions: Read and write</b>
        (esta última só para o botão de e-mail de teste). Gere e copie o token.</li>
    </ol>
    <input id="cfg-token" type="password" placeholder="${c.token ? "token já salvo neste navegador — cole outro para trocar" : "github_pat_..."}" style="width:100%">
    <div class="m-acoes">
      <button id="cfg-salvar2" class="btn prim">Salvar token</button>
      ${c.token ? `<button id="cfg-teste2" class="btn">Enviar e-mail de teste</button><button id="cfg-sair" class="btn">Esquecer token</button>` : ""}
      <span id="cfg-msg" class="aviso">${c.token ? (c.pendentes ? "Token configurado, mas há favoritos ainda não enviados ao GitHub" : "Token configurado ✔") : (c.pendentes ? "⚠ favoritos salvos só neste navegador" : "")}</span>
    </div>
    <p class="aviso">Lembretes: o GitHub confere a cada ~15 min (às vezes atrasa até 1 h) e manda e-mail para o endereço
      configurado no repositório. Ao marcar um lembrete, chega um e-mail de confirmação em alguns minutos.
      O token fica guardado só neste navegador.</p>`;
  $("#cfg-salvar2").addEventListener("click", async () => {
    const t = $("#cfg-token").value.trim();
    if (!t) return;
    $("#cfg-msg").textContent = "conferindo…";
    const r = await fetch(`https://api.github.com/repos/${c.repo}`, { headers: { Authorization: "Bearer " + t } });
    const j = r.ok ? await r.json() : {};
    if (!r.ok || !(j.permissions && j.permissions.push)) { $("#cfg-msg").textContent = "✘ token sem permissão de escrita nesse repositório"; return; }
    localStorage.setItem("gh_token", t);
    await window.FavsGitHub.carregar();  // mescla o arquivo do GitHub com o que foi salvo aqui e envia
    $("#cfg-msg").textContent = window.FavsGitHub.temPendentes()
      ? "✘ token salvo, mas não consegui gravar os favoritos no GitHub — confira a permissão Contents"
      : "✔ token salvo — favoritos sincronizados com o GitHub";
    recarregar();
  });
  $("#cfg-teste2")?.addEventListener("click", async () => {
    $("#cfg-msg").textContent = "pedindo ao GitHub…";
    const r = await fetch(`https://api.github.com/repos/${c.repo}/actions/workflows/lembretes.yml/dispatches`, {
      method: "POST", headers: { Authorization: "Bearer " + localStorage.getItem("gh_token"), Accept: "application/vnd.github+json",
        "Content-Type": "application/json" }, body: JSON.stringify({ ref: "main", inputs: { teste: "true" } }) });
    $("#cfg-msg").textContent = r.ok ? "✔ pedido enviado — o e-mail de teste chega em 1–3 min"
      : r.status === 403 || r.status === 404 ? "✘ o token não tem a permissão Actions: Read and write" : `✘ GitHub respondeu ${r.status}`;
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

// ---------------------------------------------------------------- calculadora de arrematação
const normTx = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
function lerNum(v) {  // aceita "50.000,00", "50000", "5,5", "R$ 31.000"
  let t = String(v ?? "").replace(/[R$\s%]/g, "");
  if (!t) return null;
  if (t.includes(",")) t = t.replace(/\./g, "").replace(",", ".");
  else if (/^\d{1,3}(\.\d{3})+$/.test(t)) t = t.replace(/\./g, "");
  const n = parseFloat(t);
  return isFinite(n) ? n : null;
}
const fmtNum = v => v == null ? "" : Number(v).toLocaleString("pt-BR", { maximumFractionDigits: 2 });
const brl2 = v => v == null ? "—" : v.toLocaleString("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 });
const CALC_PADRAO = { pct: 70, com: 5, taxas: {} };
let calcCtx = {};  // { salvarNota } quando aberta a partir de um veículo
function calcPref() { try { return { ...CALC_PADRAO, ...JSON.parse(localStorage.getItem("calc_pref")) }; } catch (e) { return { ...CALC_PADRAO }; } }
function calcGuardarPref() {
  const taxas = {};
  document.querySelectorAll(".c-taxa").forEach(i => { if (lerNum(i.value) != null) taxas[i.dataset.k] = lerNum(i.value); });
  localStorage.setItem("calc_pref", JSON.stringify({ pct: lerNum($("#c-pct").value) ?? CALC_PADRAO.pct,
    com: lerNum($("#c-com").value) ?? CALC_PADRAO.com, taxas }));
}
function calcular() {
  const fipe = lerNum($("#c-fipe").value), pct = lerNum($("#c-pct").value) ?? 0, com = lerNum($("#c-com").value) ?? 0;
  const lance = lerNum($("#c-lance").value);
  let taxas = 0;
  document.querySelectorAll(".c-taxa").forEach(i => { taxas += lerNum(i.value) || 0; });
  const r = { fipe, pct, com, lance, taxas, max: null };
  if (fipe) {
    r.teto = fipe * pct / 100;
    r.max = Math.max(0, (r.teto - taxas) / (1 + com / 100));  // lance + lance×com + taxas = teto
  }
  if (lance != null) {
    r.comissao = lance * com / 100;
    r.custo = lance + r.comissao + taxas;
    if (fipe) r.pctFipe = r.custo / fipe * 100;
  }
  return r;
}
function calcRender() {
  const r = calcular();
  calcCtx.ultimo = r;
  const linha = (k, v, cls = "") => `<div class="linha"><span>${k}</span><b class="${cls}">${v}</b></div>`;
  let h = "";
  if (r.fipe) {
    h += `<div>Lance máximo</div><div class="grande">${brl2(r.max)}</div>`;
    h += linha(`Teto do custo total (${fmtNum(r.pct)}% da FIPE)`, brl2(r.teto));
    h += linha("Taxas fixas", brl2(r.taxas));
    h += linha(`Comissão do leiloeiro no lance máximo (${fmtNum(r.com)}%)`, brl2(r.max * r.com / 100));
    if (r.max === 0) h += `<div class="ruim">As taxas fixas já passam do teto: não compensa com esses números.</div>`;
  } else h += `<div class="aviso">Informe o valor FIPE para calcular o lance máximo.</div>`;
  if (r.lance != null) {
    h += `<div style="margin-top:6px"><b>Com lance de ${brl2(r.lance)}</b></div>`;
    h += linha(`Comissão (${fmtNum(r.com)}%)`, brl2(r.comissao));
    h += linha("Custo total (lance + comissão + taxas)", brl2(r.custo));
    if (r.fipe) {
      const ok = r.lance <= r.max;
      h += linha("Custo em % da FIPE", fmtNum(Math.round(r.pctFipe * 10) / 10) + "%", ok ? "ok" : "ruim");
      h += linha(ok ? "Folga até o lance máximo" : "Passou do lance máximo em", brl2(Math.abs(r.max - r.lance)), ok ? "ok" : "ruim");
      h += linha("Economia vs. FIPE", brl2(r.fipe - r.custo), r.fipe - r.custo >= 0 ? "ok" : "ruim");
    }
  }
  $("#calc-res").innerHTML = h;
  $("#calc-nota").hidden = !(calcCtx.salvarNota && r.max != null);
}

// FIPE (API pública parallelum, sem cadastro)
const FIPE_API = "https://parallelum.com.br/fipe/api/v1";
const fipeCache = new Map();
async function fipeGet(caminho) {
  if (!fipeCache.has(caminho)) fipeCache.set(caminho, fetch(FIPE_API + caminho).then(r => {
    if (!r.ok) throw new Error(r.status === 429 ? "limite de consultas da FIPE atingido — tente mais tarde ou digite o valor" : "FIPE respondeu " + r.status);
    return r.json();
  }).catch(e => { fipeCache.delete(caminho); throw e; }));
  return fipeCache.get(caminho);
}
let fipeModelos = [], fipeDica = {};
const fpOpcoes = (sel, itens, rot) => { $(sel).innerHTML = `<option value="">${rot}</option>` + itens.map(x => `<option value="${esc(x.codigo)}">${esc(x.nome)}</option>`).join(""); };
async function fipeMarcas() {
  $("#fp-msg").textContent = "carregando marcas…";
  fpOpcoes("#fp-modelo", [], "modelo…"); fpOpcoes("#fp-ano", [], "ano…"); fipeModelos = [];
  try {
    const marcas = await fipeGet(`/${$("#fp-tipo").value}/marcas`);
    fpOpcoes("#fp-marca", marcas, "marca…");
    $("#fp-msg").textContent = "";
    const dica = normTx(fipeDica.marca).trim();
    if (dica) {  // "VOLKSWAGEN" → "VW - VolksWagen", "CHEVROLET" → "GM - Chevrolet"
      const so = x => normTx(x).replace(/[^a-z0-9]/g, "");
      const m = marcas.find(x => so(x.nome) === so(dica))
        || marcas.find(x => normTx(x.nome).split(/[^a-z0-9]+/).includes(dica.split(/\s+/)[0]))
        || marcas.find(x => so(x.nome).includes(so(dica)));
      if (m) { $("#fp-marca").value = m.codigo; await fipeCarregarModelos(); }
    }
  } catch (e) { $("#fp-msg").textContent = "✘ " + e.message; }
}
async function fipeCarregarModelos() {
  fpOpcoes("#fp-modelo", [], "modelo…"); fpOpcoes("#fp-ano", [], "ano…"); fipeModelos = [];
  if (!$("#fp-marca").value) return;
  $("#fp-msg").textContent = "carregando modelos…";
  try {
    fipeModelos = (await fipeGet(`/${$("#fp-tipo").value}/marcas/${$("#fp-marca").value}/modelos`)).modelos || [];
    $("#fp-msg").textContent = "";
    fipeFiltrar();
  } catch (e) { $("#fp-msg").textContent = "✘ " + e.message; }
}
function fipeFiltrar() {
  const termos = normTx($("#fp-filtro").value).match(/[a-z0-9.]+/g) || [];
  const lista = fipeModelos.filter(m => { const n = normTx(m.nome); return termos.every(t => n.includes(t)); });
  fpOpcoes("#fp-modelo", lista, lista.length ? `modelo… (${lista.length})` : "nenhum modelo com esse filtro");
  if (lista.length === 1) { $("#fp-modelo").value = lista[0].codigo; fipeCarregarAnos(); }
}
async function fipeCarregarAnos() {
  fpOpcoes("#fp-ano", [], "ano…");
  if (!$("#fp-modelo").value) return;
  try {
    const anos = await fipeGet(`/${$("#fp-tipo").value}/marcas/${$("#fp-marca").value}/modelos/${$("#fp-modelo").value}/anos`);
    fpOpcoes("#fp-ano", anos, "ano…");
    const a = fipeDica.ano && anos.find(x => x.nome.startsWith(String(fipeDica.ano)));
    if (a) { $("#fp-ano").value = a.codigo; fipeValor(); }
  } catch (e) { $("#fp-msg").textContent = "✘ " + e.message; }
}
async function fipeValor() {
  if (!$("#fp-ano").value) return;
  $("#fp-msg").textContent = "consultando valor…";
  try {
    const v = await fipeGet(`/${$("#fp-tipo").value}/marcas/${$("#fp-marca").value}/modelos/${$("#fp-modelo").value}/anos/${$("#fp-ano").value}`);
    $("#c-fipe").value = fmtNum(lerNum(v.Valor));
    $("#fp-msg").textContent = `${v.Modelo} ${v.AnoModelo} · ${v.Combustivel} · ${v.MesReferencia.trim()} · código ${v.CodigoFipe}`;
    calcRender();
  } catch (e) { $("#fp-msg").textContent = "✘ " + e.message; }
}

function abrirCalc(pre = {}) {
  const pref = calcPref();
  calcCtx = { salvarNota: pre.salvarNota || null };
  fipeDica = { marca: pre.marca || "", ano: pre.ano || "" };
  $("#c-pct").value = fmtNum(pref.pct);
  $("#c-com").value = fmtNum(pref.com);
  document.querySelectorAll(".c-taxa").forEach(i => { i.value = pref.taxas[i.dataset.k] != null ? fmtNum(pref.taxas[i.dataset.k]) : ""; });
  $("#c-lance").value = pre.lance ? fmtNum(pre.lance) : "";
  $("#c-fipe").value = "";
  $("#fp-filtro").value = pre.modelo ? String(pre.modelo).split(/\s+/)[0] : "";
  $("#calc-veiculo").hidden = !pre.titulo;
  $("#calc-veiculo").textContent = pre.titulo ? `Veículo: ${pre.titulo}` : "";
  $("#calc-msg").textContent = "";
  $("#fp-tipo").value = /moto/i.test(pre.tipo || "") ? "motos" : /caminh/i.test(pre.tipo || "") ? "caminhoes" : "carros";
  calcRender();
  $("#calc").showModal();
  fipeMarcas();
}
function ligarCalc() {
  $("#btnCalc").addEventListener("click", () => abrirCalc());
  $("#calc-fechar").addEventListener("click", () => $("#calc").close());
  $("#calc").addEventListener("click", e => { if (e.target === $("#calc")) $("#calc").close(); });
  $("#fp-tipo").addEventListener("change", () => { fipeDica = {}; fipeMarcas(); });
  $("#fp-marca").addEventListener("change", fipeCarregarModelos);
  $("#fp-filtro").addEventListener("input", fipeFiltrar);
  $("#fp-modelo").addEventListener("change", fipeCarregarAnos);
  $("#fp-ano").addEventListener("change", fipeValor);
  $(".calc-corpo").addEventListener("input", e => {
    if (e.target.matches("#c-pct, #c-com, .c-taxa")) calcGuardarPref();
    if (e.target.matches("input:not(#fp-filtro)")) calcRender();
  });
  $("#calc-limpar").addEventListener("click", () => {
    localStorage.removeItem("calc_pref");
    ["#c-fipe", "#c-lance"].forEach(s => $(s).value = "");
    document.querySelectorAll(".c-taxa").forEach(i => i.value = "");
    $("#c-pct").value = fmtNum(CALC_PADRAO.pct); $("#c-com").value = fmtNum(CALC_PADRAO.com);
    calcRender();
  });
  $("#calc-nota").addEventListener("click", async () => {
    const r = calcCtx.ultimo;
    if (!r || r.max == null || !calcCtx.salvarNota) return;
    const txt = `Lance máx. ${brl2(r.max)} (FIPE ${brl2(r.fipe)}, ${fmtNum(r.pct)}%, leiloeiro ${fmtNum(r.com)}%, taxas ${brl2(r.taxas)})`;
    $("#calc-msg").textContent = "salvando…";
    await calcCtx.salvarNota(txt);
    $("#calc-msg").textContent = "✔ salvo na anotação do veículo";
  });
}

function toast(html, ms = 9000) {
  const t = $("#toast");
  t.innerHTML = html; t.hidden = false;
  clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, ms);
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
ligarCalc();
status();
recarregar();
})();
