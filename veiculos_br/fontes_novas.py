"""Fontes exclusivas do projeto nacional de veículos: VIP Leilões (HTML) e Copart (API via Playwright)."""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from urllib.parse import parse_qsl, urlencode

import leiloes_mg as B
from leiloes_mg import (M_EXTRA, M_GOV, M_JUDICIAL, UFS, VEICULOS, Fonte, FonteErro, Lote, decodifica, fmt_data,
                        limpa, num_br, parse_data, soup, tipo_veiculo, titulo_pt, txt)

log = logging.getLogger("veiculos_br")


# ======================================================================================
# VIP LEILÕES — www.vipleiloes.com.br (ASP.NET Razor; busca devolve fragmento HTML)
# ======================================================================================
VIP_URL = "https://www.vipleiloes.com.br"
VIP_POR_PAGINA = 12
VIP_MONTAS = {"1": "Conservado", "2": "Pequena Monta", "3": "Média Monta", "4": "Sinistro Recuperado",
              "5": "Sucata Aproveitável", "6": "Sucata Aproveitável com motor inservível", "7": "Sucata ferrosa",
              "8": "Sucata inservível"}
_RE_VIP_FIM = re.compile(r"vendid|encerr|cancel|retirad|conclu|arremat", re.I)


def parse_vip_cards(html: str) -> tuple[int, list[Lote]]:
    s = soup(html)
    total = 0
    if (h := s.select_one("#resultadosEncontrados")) is not None and (m := re.search(r"([\d.]+)", txt(h))):
        total = int(m[1].replace(".", ""))
    out = []
    for c in s.select("div.card-anuncio"):
        a = c.select_one("a.anc-body") or c.select_one('a[href*="/evento/anuncio/"]')
        if a is None:
            continue
        href = (a.get("href") or "").split("?")[0]
        situacao = txt(c.select_one(".situacao"))
        if _RE_VIP_FIM.search(situacao):
            continue
        m = re.search(r"-(\d+)$", href)
        h3 = txt(c.select_one(".anc-title h3"))
        info = [txt(x) for x in c.select(".anc-info span")]
        marca = (info[0] if info else "").upper()
        modelo, fab, mod = h3, "", ""
        if mm := re.match(r"(.*?)\s*-\s*(\d{4})\s*/\s*(\d{4})\s*$", h3):
            modelo, fab, mod = mm[1], mm[2], mm[3]
        titulo = " ".join(v for v in (marca, modelo.upper(), f"{fab}/{mod}" if fab else "") if v)
        bloco = txt(c.select_one(".anc-local"))
        uf = (re.search(r"Local:\s*([A-Z]{2})\b", bloco) or [None, ""])[1]
        n_lote = (re.search(r"Lote:\s*(\d+)", bloco) or [None, ""])[1]
        n_lances = int((re.search(r"(\d+)\s*Lances?", txt(c)) or [None, "0"])[1])
        inicio = " ".join(v for v in (txt(c.select_one(".anc-start")).replace("Início:", "").strip(),
                                      txt(c.select_one(".anc-hour"))) if v)
        l = Lote(fonte="VIP Leilões", categoria=VEICULOS, modalidade=M_EXTRA, titulo=titulo or h3, link=VIP_URL + href,
                 id_fonte=m[1] if m else href, uf=uf if uf in UFS else "", status=situacao,
                 modalidade_original="VIP Leilões",
                 valor_minimo=num_br(txt(c.select_one(".anc-row-3 .anc-lel b"))) or None,
                 lance_atual=(num_br(txt(c.select_one(".valor-atual"))) or None) if n_lances else None)
        if inicio:
            l.praca("Leilão", inicio)
            l.data_encerramento = l.pracas[-1]["data"]
        l.tipo = tipo_veiculo(titulo)
        l.det("Marca", marca)
        l.det("Modelo", modelo.upper())
        if fab:
            l.det("Ano fab./modelo", f"{fab}/{mod}")
        l.det("Lote nº", n_lote)
        l.det("Lances", n_lances)
        if len(info) > 2 and (pm := re.search(r"placa final\s*(\w)", info[2], re.I)):
            l.det("Final placa", pm[1])
        if (img := c.select_one(".crd-image img")) is not None:
            l.foto(img.get("src") or "")
        out.append(l)
    return total, out


def parse_vip_detalhe(html: str, l: Lote):
    s = soup(html)
    for tr in s.select("#detalhesLote tr"):
        th, td = tr.find("th"), tr.find("td")
        if th is not None and td is not None:
            k, v = txt(th), txt(td)
            if k == "Ano":
                l.det("Ano fab./modelo", v.replace(" ", ""))
            elif k == "Veículo":
                continue
            elif k == "Observações":
                l.descricao = v
            else:
                l.det(k, v)
    d = l.detalhes
    if d.get("Comitente"):
        l.comitente = titulo_pt(d["Comitente"])
    if d.get("Localização"):
        l.endereco = d["Localização"]
        if (m := re.search(r",\s*([^,]+),\s*([A-Z]{2})\s*-\s*CEP", d["Localização"])) and m[2] in UFS:
            l.cidade, l.uf = titulo_pt(m[1]), m[2]
    if (meta := s.find("meta", attrs={"name": "description"})) is not None:
        md = meta.get("content") or ""
        if not l.cidade and (m := re.search(r"em ([^,]+?)/([A-Z]{2})\.", md)) and m[2] in UFS:
            l.cidade, l.uf = titulo_pt(m[1]), m[2]
        if m := re.search(r"Lote \d+ do leil[ãa]o (.+?)\.", md):
            l.modalidade_original = f"VIP Leilões – {m[1]}"
    if m := re.search(r"\(([^)]+)\)", d.get("Procedência", "")):
        l.det("Monta", m[1])  # "Apreensão Trânsito (Sucata Aproveitável)"
    proc = d.get("Procedência", "").lower()
    if "penhor" in proc:
        l.modalidade = M_JUDICIAL
    elif re.search(r"apreens|patrim", proc) or re.search(r"secretaria|prefeitura|detran|munic|pol[íi]cia|governo|"
                                                        r"batalh|ex[ée]rcito|tribunal|der\b|amt\b", l.comitente, re.I):
        l.modalidade = M_GOV
    for u in re.findall(r"https://armazup[\w.-]+/uploads/[\w./-]+\.(?:jpe?g|png|webp)", html, re.I):
        l.foto(u)


class VIP(Fonte):
    """A busca da VIP aceita ~8 requisições a cada ~35 s por IP (depois responde 429); as páginas de lote não têm
    esse limite. Por isso: uma passada pela busca geral bem espaçada + as páginas de lote em paralelo (monta,
    km, cidade, comitente...)."""
    chave, nome, site, categorias = "vip", "VIP Leilões", VIP_URL, (VEICULOS,)
    ESPACO = 4.2  # segundos entre buscas

    def _pagina(self, sessao, pagina: int) -> tuple[int, list[Lote]]:
        for tentativa in range(8):
            espera = self._ultima + self.ESPACO - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self._ultima = time.monotonic()
            try:
                r = self.http.post(f"{VIP_URL}/pesquisa?SortOrder=0&pageNumber={pagina}&handler=pesquisar", sessao=sessao,
                                   data={"Filtro.OrdenarPor": "DataInicio"},
                                   headers={"X-Requested-With": "XMLHttpRequest", "Referer": VIP_URL + "/pesquisa"})
                return parse_vip_cards(decodifica(r))
            except FonteErro as e:
                if "429" not in str(e):
                    raise
                log.info("[VIP] limite de requisições da busca; aguardando 40 s (página %d)", pagina)
                time.sleep(40)
        raise FonteErro("a busca da VIP continuou recusando requisições (HTTP 429)")

    def coletar(self):
        self._ultima = 0.0
        sessao = self.http.nova_sessao()
        self.http.get(VIP_URL + "/pesquisa", sessao=sessao)  # cookies da sessão
        total, cards = self._pagina(sessao, 1)
        paginas = min(math.ceil(total / VIP_POR_PAGINA), 600)
        log.info("[%s] %d anúncios em %d páginas (a busca do site é limitada: ~%.0f min)", self.nome, total, paginas,
                 paginas * self.ESPACO / 60)
        lotes: dict[str, Lote] = {}
        for pag in range(1, paginas + 1):
            if pag > 1:
                _, cards = self._pagina(sessao, pag)
            for l in cards:
                lotes.setdefault(l.id_fonte, l)
            if pag == 3 and len(lotes) <= VIP_POR_PAGINA:
                raise FonteErro("a paginação da busca da VIP repetiu sempre a mesma página (o site pode ter mudado)")
            if pag % 20 == 0:
                log.info("[%s] página %d/%d (%d anúncios)", self.nome, pag, paginas, len(lotes))
        if not lotes:
            raise FonteErro("nenhum anúncio encontrado na busca da VIP (o site pode ter mudado)")
        lista = list(lotes.values())

        def det(l: Lote):
            parse_vip_detalhe(decodifica(self.http.get(l.link, sessao=self._sessao_detalhe())), l)

        self.detalhar(lista, det)
        return lista

    def _sessao_detalhe(self):
        """Uma sessão (com cookies) por thread para as páginas de lote."""
        tl = self.__dict__.setdefault("_tl", threading.local())
        if not hasattr(tl, "s"):
            tl.s = self.http.nova_sessao()
            self.http.get(VIP_URL + "/", sessao=tl.s)
        return tl.s


# ======================================================================================
# COPART — www.copart.com.br (Imperva/Incapsula: precisa de navegador real → Playwright)
# ======================================================================================
COPART = "https://www.copart.com.br"
COPART_TAM = 500
_COPART_TIPOS = (("moto", "Moto"), ("caminh", "Caminhão / reboque"), ("rebocad", "Caminhão / reboque"),
                 ("onibus", "Ônibus"), ("ônibus", "Ônibus"), ("trator", "Trator / máquina"), ("maquin", "Trator / máquina"),
                 ("agric", "Trator / máquina"), ("embarca", "Outros"), ("jet", "Outros"))
_JS_BUSCA = """async ([corpo]) => {
  const r = await fetch('/public/lots/search', {method: 'POST', credentials: 'include', body: corpo, headers: {
    'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json, text/javascript, */*; q=0.01'}});
  return [r.status, await r.text()];
}"""


def _copart_tipo(vt: str, titulo: str) -> str:
    k = (vt or "").lower()
    for chave_, tipo in _COPART_TIPOS:
        if chave_ in k:
            return tipo
    return "Carro / utilitário" if k else tipo_veiculo(titulo)


def parse_copart(x: dict) -> Lote | None:
    ln = x.get("ln") or x.get("lotNumberStr")
    if not ln:
        return None
    marca, modelo = limpa(x.get("mkn")).upper(), limpa(x.get("lm")).upper()
    ano_mod, ano_fab = limpa(str(x.get("lcy") or "")), limpa(str(x.get("manufactureYear") or ""))
    titulo = " ".join(v for v in (marca, modelo, limpa(x.get("eng")).upper()) if v) or limpa(x.get("ld"))
    if ano_mod:
        titulo += f" {ano_fab or ano_mod}/{ano_mod}"
    patio = limpa(x.get("syn"))
    if not patio or "preenchido" in patio.lower():
        patio = titulo_pt(limpa(x.get("yn")))  # lote ainda sem leilão: usa o pátio físico
    cidade, _, uf = patio.rpartition(" - ")
    em_breve = str(x.get("upcoming")).lower() in ("sim", "true") or not limpa(x.get("ad"))
    uf = uf.strip().upper()
    hb = num_br(x.get("hb")) if x.get("hb") not in (None, "") else None
    l = Lote(fonte="Copart", categoria=VEICULOS, modalidade=M_EXTRA, titulo=titulo,
             link=f"{COPART}/lot/{ln}", id_fonte=str(ln), uf=uf if uf in UFS else "", cidade=titulo_pt(cidade),
             modalidade_original=" / ".join(v for v in ("Copart", limpa(x.get("saleType"))) if v),
             comitente=titulo_pt(limpa(x.get("sellerName"))),
             valor_avaliacao=num_br(x.get("la")) or None, lance_atual=hb or None,
             status="Compre já" if x.get("isBin") else "Em breve (sem data de leilão)" if em_breve else "Aberto para lances")
    if ad := limpa(x.get("ad")):
        l.praca("Leilão", ad)  # "2026-11-25 16:00:00"
        l.data_encerramento = l.pracas[-1]["data"]
    l.tipo = _copart_tipo(x.get("vehicleType"), titulo)
    l.det("Marca", marca)
    l.det("Modelo", modelo)
    l.det("Versão / motor", limpa(x.get("eng")))
    if ano_mod:
        l.det("Ano fab./modelo", f"{ano_fab or '?'}/{ano_mod}")
    l.det("Lote nº", ln)
    monta, sit = limpa(x.get("damageClassification")), limpa(x.get("stt") or x.get("td"))
    if "aguardando" in monta.lower():
        l.det("Classificação de monta", "Aguardando classificação")
    else:
        l.det("Monta", monta)
        l.det("Situação", sit)
    if em_breve:
        l.selo("Em breve")
    l.det("Tipo de perda", limpa(x.get("lossType")))
    l.det("Dano principal", limpa(x.get("dd")))
    l.det("Funcionamento", limpa(x.get("drivabilityRating")))
    l.det("Chassi", limpa(x.get("vinType")))
    l.det("Categoria", limpa(x.get("vehicleType")))
    l.det("Pátio", patio)
    l.det("Valor de mercado (FIPE/ACV)", B.brl(l.valor_avaliacao) if l.valor_avaliacao else None)
    l.det("Lance atual", B.brl(hb) if hb else None)
    if km := num_br(x.get("orr")):
        l.det("Quilometragem", B.fmt_num(km, 0) + " km")
    if ft := limpa(x.get("ft")):
        l.det("Combustível", ft)
    if x.get("isBin") and x.get("saleAmountBIN"):
        l.det("Compre já", B.brl(num_br(x.get("saleAmountBIN"))))
    if foto := limpa(x.get("tims")):
        l.foto(foto.replace("imageType=thumbnail", "imageType=full"))
    lt = limpa(x.get("lossType")).upper()
    if lt and lt not in ("FINANCIAMENTO",):
        l.selo(f"Sinistro: {titulo_pt(lt)}")
    return l


class Copart(Fonte):
    chave, nome, site, categorias = "copart", "Copart", COPART, (VEICULOS,)

    def coletar(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise FonteErro("o Copart precisa do Playwright: pip install playwright && python -m playwright install chromium") from e
        lotes: dict[str, Lote] = {}
        with sync_playwright() as p:
            for headless in (True, False):
                navegador = p.chromium.launch(headless=headless, args=["--disable-blink-features=AutomationControlled"])
                try:
                    corpo = self._abre_busca(navegador)
                    if corpo is None:
                        log.warning("[Copart] a busca não carregou (headless=%s)%s", headless,
                                    "; tentando com janela visível" if headless else "")
                        continue
                    self._pagina_tudo(corpo, lotes)
                    break
                finally:
                    navegador.close()
        if not lotes:
            raise FonteErro("não consegui passar pela proteção anti-robô do Copart (Incapsula)")
        log.info("[%s] %d lotes", self.nome, len(lotes))
        return list(lotes.values())

    def _abre_busca(self, navegador):
        ctx = navegador.new_context(locale="pt-BR", user_agent=B.UA, viewport={"width": 1366, "height": 900})
        self._pg = pg = ctx.new_page()
        capt: dict = {}

        def ao_pedir(r):
            if "/public/lots/search" in r.url and r.method == "POST" and "corpo" not in capt:
                capt["corpo"] = r.post_data

        pg.on("request", ao_pedir)
        pg.goto(COPART + "/", wait_until="domcontentloaded", timeout=90000)
        pg.wait_for_timeout(5000)
        pg.goto(COPART + "/lotSearchResults/?free=true&query=", wait_until="domcontentloaded", timeout=90000)
        for _ in range(60):
            if capt:
                break
            pg.wait_for_timeout(1000)
        return capt.get("corpo")

    def _pagina_tudo(self, corpo: str, lotes: dict):
        base = dict(parse_qsl(corpo, keep_blank_values=True))
        total = self._passada(base, lotes)
        if total and len(lotes) < total * 0.98:
            # a API às vezes devolve páginas vazias no meio; busca o restante pelo outro lado da ordenação
            invertida = dict(base, sort=base.get("sort", "").replace(" asc", " desc"))
            log.info("[Copart] faltaram %d lotes; segunda passada com ordem invertida", total - len(lotes))
            self._passada(invertida, lotes)
        if total and len(lotes) < total * 0.98:
            self.avisos.append(f"a API devolveu só {len(lotes)} de {total} lotes")

    def _passada(self, base: dict, lotes: dict) -> int:
        pagina, total, vazias = 0, 1, 0
        while pagina * COPART_TAM < total and pagina < 200:
            q = dict(base, draw=str(pagina + 2), start=str(pagina * COPART_TAM), length=str(COPART_TAM),
                     page=str(pagina), size=str(COPART_TAM))
            status, texto = self._pg.evaluate(_JS_BUSCA, [urlencode(list(q.items()))])
            if status != 200:
                raise FonteErro(f"HTTP {status} na busca do Copart (página {pagina})")
            res = (json.loads(texto).get("data") or {}).get("results") or {}
            total = int(res.get("totalElements") or 0) or total
            conteudo = res.get("content") or []
            if not conteudo:
                vazias += 1
                if vazias <= 3:
                    log.info("[Copart] página %d veio vazia; tentando de novo", pagina + 1)
                    self._pg.wait_for_timeout(3000 * vazias)
                    continue
                break
            vazias = 0
            novos = 0
            for x in conteudo:
                try:
                    l = parse_copart(x)
                except Exception as e:  # noqa: BLE001
                    log.debug("Copart: lote %s ignorado: %s", x.get("ln"), e)
                    continue
                if l and l.id_fonte not in lotes:
                    lotes[l.id_fonte] = l
                    novos += 1
            log.info("[Copart] página %d: +%d (total %d de %d)", pagina + 1, novos, len(lotes), total)
            pagina += 1
        return total


# ======================================================================================
# SODRÉ SANTORO — a API bloqueia scripts (WAF Azion, HTTP 403); se isso acontecer, usa um navegador real
# ======================================================================================
_JS_SODRE = """async ([corpo]) => {
  const r = await fetch('/api/search-lots', {method: 'POST', credentials: 'include', body: corpo,
    headers: {'Content-Type': 'application/json', 'Accept': 'application/json'}});
  return [r.status, await r.text()];
}"""


class SodreBR(B.Sodre):
    def coletar(self):
        try:
            return super().coletar()
        except FonteErro as e:
            if "403" not in str(e):
                raise
            log.info("[%s] API bloqueada para scripts (403); tentando pelo navegador", self.nome)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise FonteErro("a Sodré bloqueou o acesso direto e o Playwright não está instalado") from e
        lotes, vistos = [], set()
        with sync_playwright() as p:
            nav = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = nav.new_context(locale="pt-BR", user_agent=B.UA).new_page()
                pg.goto(B.SODRE + "/veiculos/lotes", wait_until="domcontentloaded", timeout=90000)
                pg.wait_for_timeout(4000)
                ini, total = 0, 1
                while ini < total and ini < 20000:
                    corpo = json.dumps({"indices": ["veiculos", "imoveis"], "query": B._SODRE_QUERY, "from": ini, "size": 500})
                    status, texto = pg.evaluate(_JS_SODRE, [corpo])
                    if status != 200:
                        raise FonteErro(f"HTTP {status} na API da Sodré, mesmo pelo navegador")
                    j = json.loads(texto)
                    res = j.get("results") or []
                    total = int(j.get("total") or 0)
                    for x in res:
                        if x.get("lot_id") in vistos:
                            continue
                        vistos.add(x.get("lot_id"))
                        try:
                            l = B.parse_sodre(x, True)
                        except Exception as e:  # noqa: BLE001
                            log.debug("Sodré: lote %s ignorado: %s", x.get("lot_id"), e)
                            continue
                        if l:
                            lotes.append(l)
                    if not res:
                        break
                    ini += len(res)
            finally:
                nav.close()
        log.info("[%s] %d lotes (via navegador)", self.nome, len(lotes))
        return lotes
