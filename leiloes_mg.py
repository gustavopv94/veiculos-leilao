#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Leilões em Minas Gerais — imóveis e veículos
=============================================

Coleta os lotes em leilão / venda em MG nas principais plataformas e gera um
relatório HTML (abas Imóveis / Veículos, separado por modalidade, com filtros,
busca, exportação CSV e todos os detalhes de cada lote).

Fontes
------
  Imóveis : Caixa Econômica Federal (venda-imoveis.caixa.gov.br)
            Portal Zuk (portalzuk.com.br)
            Mega Leilões (megaleiloes.com.br)
            Superbid Exchange / Sold (superbid.net)
            Franco Leilões (francoleiloes.com.br)
            Leilões Judiciais MG (leiloesjudiciaismg.com.br)
            GP Leilões (gpleiloes.com.br)
            Daniel Garcia Leilões (danielgarcialeiloes.com.br)
            Bolsa de Leilões (bolsadeleiloes.com.br)
  Veículos: Detran-MG (leilao.detran.mg.gov.br)
            Palácio dos Leilões (palaciodosleiloes.com.br — pátio de Juatuba/MG)
            Sodré Santoro (sodresantoro.com.br — financeiras, seguradoras e frotas)
            Mega Leilões, Superbid, Leilões Judiciais MG, GP Leilões,
            Daniel Garcia Leilões e Bolsa de Leilões

Instalação
----------
  pip install -r requirements.txt

Uso
---
  python leiloes_mg.py                         # tudo, abrindo a página de cada lote
  python leiloes_mg.py --rapido                # só as listagens (bem mais rápido)
  python leiloes_mg.py --categoria veiculos    # só veículos
  python leiloes_mg.py --fontes caixa,zuk      # só algumas fontes
  python leiloes_mg.py --sodre-todos-patios    # inclui os pátios da Sodré fora de MG
  python leiloes_mg.py --saida relatorio.html --abrir

Fontes disponíveis para --fontes: caixa, zuk, mega, superbid, franco, ljmg, gp,
danielgarcia, bolsa, detran, palacio, sodre

Observações
-----------
* Os sites mudam de layout com frequência; se uma fonte falhar, as demais
  continuam e o erro aparece no topo do relatório.
* Os dados vêm dos sites no momento da coleta. Antes de dar um lance, confira
  sempre o edital e a página oficial do lote.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import threading
import time
import unicodedata
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode, urljoin, urlsplit

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    sys.exit("Faltam dependências. Rode:  pip install requests beautifulsoup4 lxml")

import importlib.util

PARSER = "lxml" if importlib.util.find_spec("lxml") else "html.parser"

# Usa o repositório de certificados do sistema operacional (no Windows, o mesmo do navegador).
# Isso resolve sites que mandam a cadeia de certificados incompleta, sem desligar a verificação.
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001  (truststore é opcional)
    truststore = None

log = logging.getLogger("leiloes_mg")

IMOVEIS, VEICULOS = "Imóveis", "Veículos"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
}

# --------------------------------------------------------------------------------------
# Modalidades (nomes normalizados usados para agrupar o relatório)
# --------------------------------------------------------------------------------------
M_JUDICIAL = "Leilão Judicial"
M_EXTRA = "Leilão Extrajudicial"
M_DETRAN = "Leilão Público – Detran-MG"
M_GOV = "Leilão de Órgãos Públicos"
M_LICIT = "Licitação Aberta"
M_VONLINE = "Venda Online"
M_VDIRETA = "Venda Direta"
M_TOMADA = "Tomada de Preços"

ORDEM_MODALIDADES = [M_JUDICIAL, M_EXTRA, M_DETRAN, M_GOV, M_LICIT, M_VONLINE, M_VDIRETA, M_TOMADA]

DESCRICAO_MODALIDADES = {
    IMOVEIS: {
        M_JUDICIAL: "Imóveis penhorados em processos judiciais (execuções, falências, inventários etc.). "
                    "Costuma haver 1ª praça pelo valor de avaliação e 2ª praça com desconto. Leia o edital: "
                    "débitos, ocupação e o que fica por conta do arrematante variam de caso a caso.",
        M_EXTRA: "Imóveis retomados por bancos e financeiras (alienação fiduciária, Lei 9.514/97 — inclui o "
                 "Leilão SFI da Caixa) e leilões de empresas. Em geral, 1º leilão pelo valor do imóvel e "
                 "2º leilão pelo valor da dívida.",
        M_GOV: "Imóveis de prefeituras, autarquias e outros órgãos públicos.",
        M_LICIT: "Modalidade da Caixa: propostas disputadas em sessão pública com data marcada, conduzida "
                 "por leiloeiro.",
        M_VONLINE: "Modalidade da Caixa: disputa de lances pela internet durante um prazo, sem sessão presencial.",
        M_VDIRETA: "Venda por valor fixo, sem disputa: leva quem fizer primeiro a proposta válida "
                   "(ex.: Venda Direta Online da Caixa, “Valor” na Zuk).",
        M_TOMADA: "Propostas fechadas recebidas até uma data-limite; o vendedor escolhe a melhor.",
    },
    VEICULOS: {
        M_JUDICIAL: "Veículos penhorados em processos judiciais. Confira no edital débitos, restrições e "
                    "condições de retirada.",
        M_DETRAN: "Veículos removidos ou apreendidos e não retirados nos pátios credenciados do Detran-MG. "
                  "“Conservado” pode voltar a circular; “Sucata” é só para desmontagem/reciclagem (sem documento).",
        M_GOV: "Frota e bens de prefeituras, autarquias e órgãos públicos.",
        M_EXTRA: "Veículos de financeiras (recuperados de financiamento), seguradoras (sinistrados), "
                 "locadoras e empresas.",
        M_VDIRETA: "Venda por valor fixo, sem disputa de lances.",
        M_TOMADA: "Propostas fechadas recebidas até uma data-limite; o vendedor escolhe a melhor.",
    },
}

RE_JUDICIAL = re.compile(r"tribunal|justi[çc]a|\bvara\b|ju[ií]zo|\btj[a-z]{2}\b|\btrt\b|\btrf\b|judicial", re.I)


# --------------------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------------------
class FonteErro(Exception):
    """Erro esperado ao coletar uma fonte (mensagem amigável)."""


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html or "", PARSER)


def limpa(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def txt(el, sep: str = " ") -> str:
    return limpa(el.get_text(sep)) if el is not None else ""


def sem_acento(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def chave(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", sem_acento(s).lower())


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", sem_acento(s).lower()).strip("-")


def num_br(s):
    """'R$ 1.234,56' -> 1234.56 ; '41.91' -> 41.91 ; números passam direto."""
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    s = re.sub(r"[^\d,.\-]", "", str(s))
    if not s or not re.search(r"\d", s):
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def fmt_num(v, casas: int = 2) -> str:
    s = f"{v:,.{casas}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def brl(v) -> str:
    return "" if v is None else "R$ " + fmt_num(v)


RE_ISO = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?")
RE_DATA = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{2,4})(?:\D{1,8}?(\d{1,2})\s*[:hH]\s*(\d{2}))?")


def parse_data(s):
    """Entende '23/11/2026 às 15:00', '01/12/2026 - 10h00', '23/11/26', '2026-11-23 12:21:30'..."""
    if not s:
        return None
    s = str(s)
    m = RE_ISO.search(s)
    if m:
        y, mo, d, h, mi = m.groups()
    else:
        m = RE_DATA.search(s)
        if not m:
            return None
        d, mo, y, h, mi = m.groups()
    y = int(y)
    if y < 100:
        y += 2000
    try:
        return datetime(y, int(mo), int(d), int(h or 0), int(mi or 0))
    except ValueError:
        return None


def fmt_data(dt) -> str:
    if not dt:
        return ""
    return dt.strftime("%d/%m/%Y %H:%M") if (dt.hour or dt.minute) else dt.strftime("%d/%m/%Y")


_MINUSC = {"de", "da", "do", "das", "dos", "e", "em", "na", "no"}


def titulo_pt(s: str) -> str:
    """'AGUAS FORMOSAS' -> 'Aguas Formosas' (só mexe em textos todo em maiúsculas)."""
    s = limpa(s)
    if not s or not s.isupper():
        return s
    out = []
    for i, w in enumerate(s.lower().split(" ")):
        out.append(w if (i and w in _MINUSC) else w[:1].upper() + w[1:])
    return " ".join(out)


def html_para_texto(h: str) -> str:
    if not h:
        return ""
    s = soup(str(h).replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n"))
    for p in s.find_all(["p", "li", "div"]):
        p.append("\n")
    t = s.get_text()
    t = t.replace("\xa0", " ").replace(" ¿ ", " – ").replace("¿", "–")
    linhas = [limpa(x) for x in t.splitlines()]
    out, vazio = [], False
    for ln in linhas:
        if ln:
            out.append(ln)
            vazio = False
        elif not vazio and out:
            out.append("")
            vazio = True
    return "\n".join(out).strip()


def decodifica(r) -> str:
    bruto = r.content
    for enc in ("utf-8", "cp1252"):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            continue
    return bruto.decode("utf-8", "replace")


RE_CNJ = re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")

# Heurística para o tipo de veículo quando a fonte não informa (Detran-MG, Palácio)
_RE_ONIBUS = re.compile(r"\b(onibus|marcopolo|busscar|comil|caio|neobus|mascarello|micro ?onibus|induscar)\b")
_RE_CAMINHAO = re.compile(r"\b(caminhao|scania|iveco|daf|atego|axor|accelo|actros|constellation|worker|delivery|"
                          r"cargo \d|volvo (fh|fm|vm|nh|nl)|mb l ?\d{4}|m\.?benz l|\d{1,2}\.\d{3}|\d{2}-\d{3}|"
                          r"carreta|semi ?reboque|reboque|cavalo mecanico|truck)\b")
_RE_MOTO = re.compile(r"\b(moto|motocicleta|motoneta|ciclomotor|triciclo|quadriciclo|yamaha|kawasaki|harley|ducati|"
                      r"triumph|royal enfield|sundown|garinni|shineray|dafra|traxx|kasinski|haojue|bravax|jtz|avelloz|"
                      r"cg ?\d{3}|cb ?\d{3}|cbx|cb ?twister|xre|nxr|bros|biz|pop ?1\d0|pcx|elite|titan|fan ?1\d0|"
                      r"start ?1\d0|lead ?1\d0|xl ?\d{3}|sahara|ybr|factor|fazer|crypton|neo ?1\d\d|lander|tenere|xtz|"
                      r"burgman|intruder|yes ?125|gsx|v-?strom|hayabusa|hunter ?1\d0|gr ?150|ninja|z ?\d{3,4}|"
                      r"g ?310|f ?[78]\d0 ?gs|r ?1[2-3]\d0)\b")


def tipo_veiculo(texto: str) -> str:
    k = sem_acento(texto or "").lower()
    if _RE_ONIBUS.search(k):
        return "Ônibus"
    if _RE_MOTO.search(k):
        return "Moto"
    if _RE_CAMINHAO.search(k):
        return "Caminhão / reboque"
    return "Carro / utilitário" if k.strip() else "Veículo"


_TIPOS_IMOVEL = (
    (r"apartament|\bapto\b|cobertura|kitnet|\bflat\b", "Apartamento"),
    (r"\bcasa\b|sobrado|residencia", "Casa"),
    (r"fazenda|\bsitio\b|chacara|gleba|rural|\bterras\b|hectare|alqueire", "Rural"),
    (r"galpao|armazem|industrial", "Galpão / industrial"),
    (r"\bsala\b|\bloja\b|predio|comercial|escritorio|salao", "Comercial"),
    (r"\bvaga\b|garagem", "Vaga de garagem"),
    (r"terreno|\blote\b", "Terreno"),
)


def tipo_imovel(texto: str, padrao: str = "Imóvel") -> str:
    k = sem_acento(texto or "").lower()
    for pad, nome in _TIPOS_IMOVEL:
        if re.search(pad, k):
            return nome
    return padrao


UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR", "PE",
       "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
_RE_CIDADE_UF = re.compile(r"([A-Za-zÀ-ÿ'.\- ]{2,40}?)\s*/\s*([A-Z]{2})\b")
_RE_MG = re.compile(r"minas gerais|\btj ?/? ?mg\b|\btrt ?-? ?3\b|\btrf ?-? ?6\b", re.I)


# Escopo geográfico da coleta: {"MG"} (padrão deste script) ou None = Brasil todo.
ESCOPO_UFS: set | None = {"MG"}

NOME_UF = {"acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
           "distritofederal": "DF", "espiritosanto": "ES", "goias": "GO", "maranhao": "MA", "matogrosso": "MT",
           "matogrossodosul": "MS", "minasgerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR",
           "pernambuco": "PE", "piaui": "PI", "riodejaneiro": "RJ", "riograndedonorte": "RN",
           "riograndedosul": "RS", "rondonia": "RO", "roraima": "RR", "santacatarina": "SC", "saopaulo": "SP",
           "sergipe": "SE", "tocantins": "TO"}


def definir_escopo(ufs) -> None:
    """None/vazio = Brasil todo; senão, conjunto de UFs aceitas."""
    global ESCOPO_UFS
    ESCOPO_UFS = {u.upper() for u in ufs} if ufs else None


def nacional() -> bool:
    return ESCOPO_UFS is None


def aceita_uf(uf: str, vazio: bool = False) -> bool:
    """A UF está no escopo da coleta? `vazio` = o que responder quando a UF é desconhecida."""
    if ESCOPO_UFS is None:
        return True
    uf = (uf or "").strip().upper()
    return uf in ESCOPO_UFS if uf else vazio


def uf_por_nome(s: str) -> str:
    """'Minas Gerais' -> 'MG'; 'SP' -> 'SP'."""
    s = (s or "").strip()
    if s.upper() in UFS:
        return s.upper()
    return NOME_UF.get(chave(s), "")


def uf_do_texto(texto: str) -> str:
    """Primeira 'Cidade/UF' válida do texto (ou 'MG' se citar Minas Gerais / TJMG)."""
    for m in _RE_CIDADE_UF.finditer(texto or ""):
        if m[2] in UFS:
            return m[2]
    return "MG" if _RE_MG.search(texto or "") else ""


def conserta_acentos(s: str) -> str:
    """'ComissÃ£o' -> 'Comissão' (texto UTF-8 que foi lido como Latin-1)."""
    if s and ("Ã" in s or "Â" in s):
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return s
    return s or ""


_NUXT_EMBRULHOS = {"Reactive", "ShallowReactive", "Ref", "ShallowRef", "EmptyRef", "EmptyShallowRef", "NuxtError"}


def nuxt_data(html: str):
    """Decodifica o <script id="__NUXT_DATA__"> (formato 'devalue' dos sites em Nuxt 3)."""
    m = re.search(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', html or "", re.S)
    if not m:
        return None
    bruto = json.loads(m[1])
    memo: dict[int, object] = {}

    def hid(i):
        if isinstance(i, bool) or not isinstance(i, int):
            return i
        if i < 0:
            return None
        if i in memo:
            return memo[i]
        v = bruto[i]
        if isinstance(v, list):
            if v and isinstance(v[0], str):
                tag = v[0]
                if tag in _NUXT_EMBRULHOS:
                    memo[i] = hid(v[1]) if len(v) > 1 else None
                elif tag == "Date":
                    memo[i] = v[1]
                elif tag == "Set":
                    memo[i] = [hid(x) for x in v[1:]]
                elif tag in ("Map", "null"):
                    memo[i] = {str(hid(v[j])): hid(v[j + 1]) for j in range(1, len(v) - 1, 2)}
                else:
                    memo[i] = None
                return memo[i]
            out: list = []
            memo[i] = out
            out.extend(hid(x) for x in v)
            return out
        if isinstance(v, dict):
            obj: dict = {}
            memo[i] = obj
            for k, x in v.items():
                obj[k] = hid(x)
            return obj
        memo[i] = v
        return v

    return hid(0)


# --------------------------------------------------------------------------------------
# HTTP com limite de ritmo por site e novas tentativas
# --------------------------------------------------------------------------------------
class Http:
    def __init__(self, intervalo: float = 0.15, timeout: int = 45, tentativas: int = 3):
        self.intervalo, self.timeout, self.tentativas = intervalo, timeout, tentativas
        self._tl = threading.local()
        self._ultimo: dict[str, float] = {}
        self._lock = threading.Lock()

    def nova_sessao(self) -> requests.Session:
        s = requests.Session()
        s.headers.update(HEADERS)
        return s

    def _sessao(self) -> requests.Session:
        if not hasattr(self._tl, "s"):
            self._tl.s = self.nova_sessao()
        return self._tl.s

    def _espera(self, host: str):
        with self._lock:
            agora = time.monotonic()
            vez = max(agora, self._ultimo.get(host, 0.0) + self.intervalo)
            self._ultimo[host] = vez
        if vez > agora:
            time.sleep(vez - agora)

    def req(self, metodo: str, url: str, sessao=None, aceita404: bool = False, **kw):
        host = urlsplit(url).netloc
        erro = None
        for t in range(1, self.tentativas + 1):
            self._espera(host)
            try:
                r = (sessao or self._sessao()).request(metodo, url, timeout=self.timeout, **kw)
            except requests.exceptions.SSLError as e:
                dica = "" if truststore else " Instale o pacote truststore (pip install truststore) e rode de novo."
                raise FonteErro(f"não foi possível verificar o certificado de segurança de {host}.{dica} "
                                f"Detalhe: {e}") from e
            except requests.RequestException as e:
                erro = e
                time.sleep(1.5 * t)
                continue
            if r.status_code in (429, 500, 502, 503, 504) and t < self.tentativas:
                erro = f"HTTP {r.status_code}"
                time.sleep(2.0 * t)
                continue
            if r.status_code == 404 and aceita404:
                return r
            if r.status_code >= 400:
                raise FonteErro(f"HTTP {r.status_code} em {url}")
            return r
        raise FonteErro(f"Falha ao acessar {url}: {erro}")

    def get(self, url, **kw):
        return self.req("GET", url, **kw)

    def post(self, url, **kw):
        return self.req("POST", url, **kw)


# --------------------------------------------------------------------------------------
# Modelo do lote
# --------------------------------------------------------------------------------------
@dataclass
class Lote:
    fonte: str
    categoria: str
    modalidade: str
    titulo: str
    link: str
    modalidade_original: str = ""
    id_fonte: str = ""
    tipo: str = ""
    descricao: str = ""
    cidade: str = ""
    bairro: str = ""
    endereco: str = ""
    uf: str = "MG"
    valor_avaliacao: float | None = None
    valor_minimo: float | None = None
    lance_atual: float | None = None
    desconto: float | None = None
    pracas: list = field(default_factory=list)
    data_encerramento: str = ""
    status: str = ""
    leiloeiro: str = ""
    comitente: str = ""
    processo: str = ""
    selos: list = field(default_factory=list)
    fotos: list = field(default_factory=list)
    documentos: list = field(default_factory=list)
    detalhes: dict = field(default_factory=dict)

    # campos calculados em finalizar()
    preco_ref: float | None = None
    data_ref: str = ""

    def det(self, k: str, v):
        """Guarda um detalhe extra (ignora vazios)."""
        if v is None:
            return
        if isinstance(v, bool):
            v = "Sim" if v else "Não"
        v = limpa(v)
        if v and v not in ("-", "/", "//", "None", "null", "R$ 0,00", "0,00"):
            self.detalhes[limpa(k).rstrip(":")] = v

    def doc(self, nome: str, url: str):
        url = (url or "").strip()
        if url.startswith("http") and all(d["url"] != url for d in self.documentos):
            self.documentos.append({"nome": limpa(nome) or "Documento", "url": url})

    def foto(self, url: str):
        url = (url or "").strip()
        if url.startswith("http") and url not in self.fotos:
            self.fotos.append(url)

    def praca(self, nome: str, data=None, valor=None):
        dt = parse_data(data) if not isinstance(data, datetime) else data
        self.pracas.append({
            "nome": limpa(nome),
            "data": fmt_data(dt) if dt else limpa(data),
            "iso": dt.isoformat() if dt else "",
            "valor": valor,
        })

    def selo(self, s: str):
        s = limpa(s)
        if s and s not in self.selos:
            self.selos.append(s)


# --------------------------------------------------------------------------------------
# Base das fontes
# --------------------------------------------------------------------------------------
@dataclass
class Config:
    detalhes: bool = True
    workers: int = 6
    max_detalhes: int = 0
    caixa_csv: str = ""
    sodre_todos: bool = False


class Fonte:
    chave = ""
    nome = ""
    site = ""
    categorias: tuple = ()

    def __init__(self, http: Http, cfg: Config):
        self.http, self.cfg = http, cfg
        self.avisos: list[str] = []

    def coletar(self) -> list[Lote]:  # pragma: no cover
        raise NotImplementedError

    def detalhar(self, lotes: list[Lote], func):
        if not self.cfg.detalhes or not lotes:
            return
        alvo = lotes[: self.cfg.max_detalhes] if self.cfg.max_detalhes else lotes
        log.info("[%s] abrindo a página de %d lotes para pegar os detalhes...", self.nome, len(alvo))
        falhas, feitos, t0 = 0, 0, time.time()
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.workers)) as ex:
            futuros = {ex.submit(func, l): l for l in alvo}
            for fut in as_completed(futuros):
                feitos += 1
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001
                    falhas += 1
                    log.debug("[%s] detalhe falhou (%s): %s", self.nome, futuros[fut].link, e)
                if feitos % 100 == 0 or feitos == len(alvo):
                    log.info("[%s] detalhes: %d/%d (%.0fs)", self.nome, feitos, len(alvo), time.time() - t0)
        if falhas:
            self.avisos.append(f"{falhas} de {len(alvo)} páginas de detalhe não puderam ser lidas")


# ======================================================================================
# 1) CAIXA — imóveis
# ======================================================================================
CAIXA = "https://venda-imoveis.caixa.gov.br"


def modalidade_caixa(m: str) -> str:
    k = sem_acento(m).lower()
    if "leil" in k:
        return M_EXTRA
    if "licita" in k or "concorr" in k:
        return M_LICIT
    if "venda direta" in k:
        return M_VDIRETA
    if "venda online" in k:
        return M_VONLINE
    return limpa(m) or "Outros"


def parse_caixa_descricao(d: str):
    partes = [p.strip() for p in (d or "").strip().rstrip(".").split(",") if p.strip()]
    if not partes:
        return "Imóvel", {}
    tipo, ext, comodos = partes[0], {}, []
    for p in partes[1:]:
        if m := re.match(r"([\d.]+)\s+de [áa]rea (total|privativa|do terreno)", p, re.I):
            v = float(m[1])
            if v > 0:
                ext[f"Área {m[2].lower()}"] = f"{fmt_num(v)} m²"
        elif m := re.match(r"(\d+)\s*qto", p, re.I):
            ext["Quartos"] = m[1]
        elif m := re.match(r"(\d+)\s*vaga", p, re.I):
            ext["Vagas de garagem"] = m[1]
        elif m := re.match(r"(\d+)\s*sala", p, re.I):
            ext["Salas"] = m[1]
        else:
            comodos.append(p.replace("a.serv", "área de serviço"))
    if comodos:
        ext["Cômodos"] = ", ".join(comodos)
    return tipo, ext


def parse_caixa_csv(texto: str) -> list[Lote]:
    linhas = texto.splitlines()
    ini = next((i for i, l in enumerate(linhas) if ";" in l and "modalidade" in sem_acento(l).lower()), None)
    if ini is None:
        raise FonteErro("cabeçalho do CSV da Caixa não encontrado (o formato pode ter mudado)")
    leitor = csv.reader(linhas[ini:], delimiter=";")
    cab = [chave(c) for c in next(leitor)]

    def col(row, *nomes):
        for n in nomes:
            for i, c in enumerate(cab):
                if c.startswith(n) and i < len(row):
                    return row[i].strip()
        return ""

    lotes = []
    for row in leitor:
        if len(row) < 6 or not row[0].strip():
            continue
        num = re.sub(r"\D", "", col(row, "ndoimovel", "numero", "n"))
        uf = col(row, "uf").upper()
        if not num or (uf and uf != "MG"):
            continue
        cidade, bairro = titulo_pt(col(row, "cidade")), titulo_pt(col(row, "bairro"))
        modal = col(row, "modalidade")
        descricao = col(row, "descricao")
        tipo, ext = parse_caixa_descricao(descricao)
        l = Lote(
            fonte="Caixa", categoria=IMOVEIS, modalidade=modalidade_caixa(modal),
            modalidade_original=f"Caixa – {modal}" if modal else "Caixa",
            titulo=f"{tipo} – {bairro}, {cidade}" if bairro else f"{tipo} – {cidade}",
            link=col(row, "link") or f"{CAIXA}/sistema/detalhe-imovel.asp?hdnOrigem=index&hdnimovel={num}",
            id_fonte=num, tipo=tipo, descricao=descricao, cidade=cidade, bairro=bairro,
            endereco=limpa(col(row, "endereco")),
            valor_avaliacao=num_br(col(row, "valordeavaliacao", "avaliacao")),
            valor_minimo=num_br(col(row, "preco")),
            desconto=num_br(col(row, "desconto")) or None,
            comitente="Caixa Econômica Federal",
        )
        l.det("Nº do imóvel (Caixa)", num)
        l.det("Aceita financiamento", col(row, "financiamento"))
        for k, v in ext.items():
            l.det(k, v)
        l.foto(f"{CAIXA}/fotos/F{num.zfill(13)}21.jpg")
        lotes.append(l)
    return lotes


def parse_caixa_detalhe(html: str, l: Lote):
    s = soup(html)
    box = s.select_one("#dadosImovel")
    if box is None:
        raise FonteErro("página de detalhe sem #dadosImovel")
    nome = txt(box.find("h5"))
    if nome and " - " not in nome:
        l.det("Empreendimento", nome)

    cont = box.select_one(".content") or box
    p0 = txt(cont.find("p"))
    if m := re.search(r"Valor de avalia\w*:\s*R\$\s*([\d.,]+)", p0):
        l.valor_avaliacao = num_br(m[1]) or l.valor_avaliacao
    valores = [num_br(v) for v in re.findall(r"Valor m[íi]nimo de venda[^:]*:\s*R\$\s*([\d.,]+)", p0)]
    if m := re.search(r"desconto de\s*([\d.,]+)\s*%", p0):
        l.desconto = num_br(m[1])
    for sp in cont.select("span"):
        m = re.match(r"([^:=]{2,60}?)\s*[:=]\s*(.+)$", txt(sp))
        if m:
            l.det(m[1], re.sub(r"^([\d.,]+)\s*m2$", r"\1 m²", m[2].strip()))

    rb = box.select_one(".related-box")
    if rb is not None:
        b = rb.find("b")
        if b:
            l.det("Modalidade (Caixa)", txt(b))
        texto_rb = limpa(rb.get_text(" "))
        for sp in rb.find_all("span"):
            t = txt(sp)
            if m := re.match(r"Edital:\s*(.+)", t):
                l.det("Edital", m[1])
            elif m := re.match(r"N[úu]mero do item:\s*(.+)", t):
                l.det("Item do edital", m[1])
            elif m := re.match(r"Leiloeiro\(a\):\s*(.+)", t):
                l.leiloeiro = m[1]
        datas = re.findall(r"Data d[oae]s?\s+(.+?)\s+-\s+(\d{2}/\d{2}/\d{4})\s+-\s+(\d{1,2})h(\d{2})", texto_rb)
        if datas:
            l.pracas = []
            for i, (nm, d, h, mi) in enumerate(datas):
                l.praca(nm, f"{d} {h}:{mi}", valores[i] if i < len(valores) else None)
        for p in rb.find_all("p"):
            st = p.find("strong")
            if not st:
                continue
            rot = txt(st).rstrip(":").lower()
            corpo = limpa(p.get_text(" ").replace(st.get_text(), "", 1))
            if rot.startswith("endere") and corpo:
                l.endereco = corpo
                if m := re.search(r"CEP:\s*([\d-]{8,9})", corpo):
                    l.det("CEP", m[1])
            elif rot.startswith("descri") and corpo.strip(" .") and corpo.strip(" .") not in l.descricao:
                l.descricao = f"{l.descricao}\n{corpo}".strip()
        bloco = rb.get_text("\n")
        if "FORMAS DE PAGAMENTO" in bloco:
            resto = bloco.split("FORMAS DE PAGAMENTO", 1)[1].split(":", 1)[-1]
            pag, _, desp = resto.partition("REGRAS PARA PAGAMENTO")
            itens = [limpa(x) for x in pag.splitlines() if limpa(x)]
            l.det("Formas de pagamento", " • ".join(itens))
            desp = desp.split("Corretores credenciados")[0]
            desp_itens = [limpa(x) for x in desp.splitlines() if limpa(x) and "caso existam" not in x]
            if desp_itens:
                l.det("Regras de despesas", " • ".join(desp_itens))
    for el in box.select("[onclick*=ExibeDoc]"):
        if m := re.search(r"ExibeDoc\('([^']+)'\)", el.get("onclick", "")):
            l.doc(txt(el) or "Documento", urljoin(CAIXA + "/", m[1]))
    for el in box.select("[onclick*=SiteLeiloeiro]"):
        if m := re.search(r"SiteLeiloeiro\([\"']([^\"']+)", el.get("onclick", "")):
            site = re.sub(r"^https?://", "", m[1])
            l.doc("Site do leiloeiro (para dar lance)", "https://" + site)
            l.det("Site do leiloeiro", site)
    for img in s.select("#galeria-imagens img"):
        l.foto(urljoin(CAIXA + "/sistema/", img.get("src", "")))


class Caixa(Fonte):
    chave, nome, site, categorias = "caixa", "Caixa Econômica Federal", CAIXA, (IMOVEIS,)

    def coletar(self):
        if self.cfg.caixa_csv:
            texto = Path(self.cfg.caixa_csv).read_bytes()
            texto = texto.decode("utf-8") if texto[:3] == b"\xef\xbb\xbf" else texto.decode("cp1252", "replace")
        else:
            try:  # pega cookies da página antes de baixar o CSV
                self.http.get(f"{CAIXA}/sistema/download-lista.asp")
            except Exception:  # noqa: BLE001
                pass
            r = self.http.get(f"{CAIXA}/listaweb/Lista_imoveis_MG.csv",
                              headers={"Referer": f"{CAIXA}/sistema/download-lista.asp", "Accept": "*/*"})
            texto = decodifica(r)
            if "<html" in texto[:1000].lower():
                raise FonteErro("a Caixa devolveu uma página em vez do CSV (bloqueio temporário?). "
                                "Baixe a lista de MG em venda-imoveis.caixa.gov.br/sistema/download-lista.asp "
                                "e rode com --caixa-csv ARQUIVO.csv")
        lotes = parse_caixa_csv(texto)
        log.info("[%s] %d imóveis na lista de MG", self.nome, len(lotes))

        def det(l: Lote):
            r = self.http.get(f"{CAIXA}/sistema/detalhe-imovel.asp?hdnOrigem=index&hdnimovel={l.id_fonte}",
                              headers={"Referer": f"{CAIXA}/sistema/busca-imovel.asp"})
            parse_caixa_detalhe(decodifica(r), l)

        self.detalhar(lotes, det)
        return lotes


# ======================================================================================
# 2) PORTAL ZUK — imóveis
# ======================================================================================
ZUK = "https://www.portalzuk.com.br"
ZUK_LISTA = ZUK + "/leilao-de-imoveis/u/todos-imoveis/mg"


def parse_zuk_cards(html: str) -> list[Lote]:
    s = soup(html)
    out = []
    for card in s.select("div.card_lotes_div"):
        a = card.select_one(".card-property-image-wrapper a") or card.find("a")
        if a is None:
            continue
        href = (a.get("href") or "").strip() or (a.get("data-url-externa") or "").strip()
        titulo_attr = limpa(a.get("title", ""))
        m = re.search(r"\s-\s([^|]+?)\s*\|\s*(Z\d+)\s*$", titulo_attr)
        comitente = limpa(a.get("data-comitente") or "")
        if not comitente and m:
            comitente = m[1].rsplit(" - ", 1)[-1].strip()
        codigo = m[2] if m else ""
        mid = re.search(r"/(\d+)-(\d+)/?$", href)
        fav = card.select_one(".card-property-favorite")
        id_lote = (mid[2] if mid else a.get("data-idlote")) or (fav.get("id") if fav else "") or codigo
        if not href:
            href = f"{ZUK_LISTA}#{id_lote}"

        tipo = txt(card.select_one(".card-property-price-lote")) or "Imóvel"
        spans = card.select(".card-property-address span")
        linha1 = txt(spans[0]) if spans else ""
        endereco = txt(spans[1]) if len(spans) > 1 else ""
        cid_uf, _, bairro = linha1.partition(" - ")
        cidade = cid_uf.split("/")[0].strip()

        pracas = []
        for li in card.select("li.card-property-price"):
            rot = li.select_one(".card-property-price-label")
            if rot is None:
                continue
            val = li.select_one(".card-property-price-value")
            pct = None
            if val is not None:
                pe = val.select_one(".card-property-price-percent")
                if pe is not None:
                    pct = num_br(txt(pe))
                    pe.extract()
            pracas.append((txt(rot), num_br(txt(val)) if val is not None else None,
                           txt(li.select_one(".card-property-price-data")), pct))

        rotulos = [sem_acento(p[0]).lower().strip() for p in pracas]
        if RE_JUDICIAL.search(comitente):
            mod = M_JUDICIAL
        elif rotulos and all(r in ("valor", "venda direta") for r in rotulos):
            mod = M_VDIRETA
        else:
            mod = M_EXTRA
        if len(pracas) >= 2:
            orig = "Zuk – 1º e 2º leilão"
        elif pracas:
            orig = {"valor": "Zuk – Venda direta (valor fixo)", "lance inicial": "Zuk – Leilão (praça única)"}.get(
                rotulos[0], f"Zuk – {pracas[0][0]}")
        else:
            orig = "Zuk"
        parceiro = card.get("data-parceiro") or "0"
        if parceiro not in ("0", ""):
            orig += f" (parceiro: {parceiro})"

        l = Lote(fonte="Portal Zuk", categoria=IMOVEIS, modalidade=mod, modalidade_original=orig,
                 titulo=f"{tipo} – {endereco}" if endereco else tipo, link=href, id_fonte=str(id_lote),
                 tipo=tipo, cidade=cidade, bairro=bairro.strip(), endereco=endereco, comitente=comitente)
        for nm, v, d, _pct in pracas:
            l.praca(nm, d, v)
        if pracas and pracas[-1][3]:
            l.desconto = pracas[-1][3]
        l.det("Código Zuk", codigo)
        l.det("Lote", a.get("title.") or "")
        for info in card.select(".card-property-info-label"):
            t = txt(info)
            if t:
                l.det("Área" if "m²" in t else "Informação", t)
        novidade = txt(card.select_one(".card-property-news"))
        if novidade:
            l.selo(novidade)
            if "ocupad" in novidade.lower():
                l.det("Ocupação", novidade)
        img = card.select_one("img")
        if img is not None:
            l.foto(img.get("src") or img.get("data-src") or "")
        out.append(l)
    return out


def parse_zuk_detalhe(html: str, l: Lote):
    s = soup(html)
    prop = s.select_one(".property-main-content") or s.select_one(".property") or s
    end = txt(prop.select_one("p.property-address"))
    if end and len(end) > len(l.endereco):
        l.endereco = re.sub(r"\s*/\s*MG$", "/MG", end)
    enc = txt(prop.select_one(".card-action-header-title"))
    if enc:
        l.data_encerramento = fmt_data(parse_data(enc)) or enc
        l.det("Encerramento", enc)
    dica = txt(prop.select_one(".card-action-header .tooltip-text"))
    l.det("Situação do leilão", dica)

    novas = []
    for li in prop.select("li.card-action-item"):
        nm = txt(li.select_one(".card-action-item-text"))
        if not nm:
            continue
        val_el = li.select_one(".card-action-item-value")
        if val_el is not None:
            for pe in val_el.select(".card-property-price-percent"):
                pe.extract()
        novas.append((nm, txt(li.select_one(".card-action-item-date")), num_br(txt(val_el))))
    if novas:
        l.pracas = []
        for nm, d, v in novas:
            l.praca(nm, d, v)

    banco = prop.select_one(".box-action-bank img")
    if banco is not None and banco.get("alt"):
        l.comitente = limpa(banco["alt"])
        if RE_JUDICIAL.search(l.comitente):
            l.modalidade = M_JUDICIAL
    lance = num_br(txt(prop.select_one("#maior-lance-vlr")))
    if lance:
        l.lance_atual = lance
    for box in prop.select(".box-action-bid"):
        rot = txt(box.select_one(".box-action-bid-label"))
        if "incremento" in rot.lower():
            l.det("Incremento mínimo", txt(box.select_one(".box-action-bid-value")))
    for it in prop.select(".property-featured-item"):
        l.det(txt(it.select_one(".property-featured-item-label")), txt(it.select_one(".property-featured-item-value")))
    stt = txt(prop.select_one(".property-status-title"))
    if stt:
        l.det("Ocupação", stt)
        if "desocup" in stt.lower() or "ocupad" in stt.lower():
            l.selo(stt)
    for info in prop.select(".property-info"):
        tit = txt(info.select_one(".property-info-title"))
        if not tit or tit.lower().startswith(("proximidades", "documentos")):
            continue
        if tit.lower().startswith("descri"):
            p = info.select_one(".property-hide-show") or info.select_one(".property-info-text")
            if p is not None:
                l.descricao = html_para_texto(str(p))
            mat = txt(info.select_one("#itens_matricula"))
            if mat:
                l.det("Matrícula", mat)
            continue
        corpo = info.select_one(".property-info-text") or info.select_one("ul")
        if corpo is not None:
            l.det(tit, txt(corpo))
    for a in prop.select("a.property-documents-item"):
        nome = txt(a.select_one(".property-documents-item-label")) or txt(a)
        href = (a.get("href") or "").strip()
        if href.startswith("http"):
            l.doc(nome, href)
        else:
            l.det(f"Documento: {nome}", "disponível no site (requer login)")
    rel = prop.select_one(".link_relacao_leilao a")
    if rel is not None:
        l.doc("Relação completa do leilão", rel.get("href", ""))
    part = prop.select_one("#btn_participar")
    if part is not None:
        l.doc("Participar do leilão (auditório)", part.get("href", ""))
    for img in (s.select_one(".property-gallery") or s).select(".property-gallery-image img"):
        l.foto(img.get("src") or img.get("data-src") or "")


class Zuk(Fonte):
    chave, nome, site, categorias = "zuk", "Portal Zuk", ZUK, (IMOVEIS,)

    def coletar(self):
        sess = self.http.nova_sessao()
        html = decodifica(self.http.get(ZUK_LISTA, sessao=sess))
        m = (re.search(r'name="_token"\s+value="([^"]+)"', html)
             or re.search(r'name="csrf-token"\s+content="([^"]+)"', html))
        token = m[1] if m else ""
        lotes = parse_zuk_cards(html)
        vistos = {l.link for l in lotes}
        n_zuk = len(re.findall(r'data-parceiro="0"', html))
        if not token:
            self.avisos.append("token da Zuk não encontrado — só a primeira página foi lida")
        for _ in range(400):
            if not token:
                break
            dados = {"limit": str(len(lotes)), "count_imovel_zuk": str(n_zuk), "path": ZUK_LISTA,
                     "order": "data_leilao", "div_parceiro_count": "0", "_token": token}
            r = self.http.post(f"{ZUK}/leilao-de-imoveis/mais", data=dados, sessao=sess, headers={
                "X-Requested-With": "XMLHttpRequest", "X-CSRF-TOKEN": token, "Referer": ZUK_LISTA,
                "Origin": ZUK, "Accept": "text/html, */*; q=0.01"})
            h = decodifica(r)
            novos = [l for l in parse_zuk_cards(h) if l.link not in vistos]
            if not novos:
                break
            vistos.update(l.link for l in novos)
            lotes += novos
            n_zuk += len(re.findall(r'data-parceiro="0"', h))
        log.info("[%s] %d imóveis em MG", self.nome, len(lotes))

        def det(l: Lote):
            parse_zuk_detalhe(decodifica(self.http.get(l.link)), l)

        self.detalhar([l for l in lotes if l.link.startswith(ZUK + "/imovel/")], det)
        return lotes


# ======================================================================================
# 3) MEGA LEILÕES — imóveis e veículos
# ======================================================================================
MEGA = "https://www.megaleiloes.com.br"


def _mega_modalidade(tipo: str) -> str:
    k = sem_acento(tipo).lower()
    if "extrajudicial" in k:
        return M_EXTRA
    if "judicial" in k:
        return M_JUDICIAL
    if "venda direta" in k:
        return M_VDIRETA
    return limpa(tipo).title() or M_EXTRA


def _logo_para_nome(src: str) -> str:
    nome = Path(urlsplit(src).path).stem
    nome = re.sub(r"^(leilao|logo)[-_]", "", nome)
    return nome.replace("-", " ").replace("_", " ").title() if nome else ""


MEGA_TIPOS = {"casas": "Casa", "apartamentos": "Apartamento", "terrenos-e-lotes": "Terreno", "terrenos": "Terreno",
              "imoveis-rurais": "Imóvel rural", "rurais": "Imóvel rural", "imoveis-comerciais": "Comercial",
              "comerciais": "Comercial", "galpoes": "Galpão", "galpoes-industriais": "Galpão",
              "carros": "Carro / utilitário", "motos": "Moto", "caminhoes": "Caminhão / reboque",
              "onibus": "Ônibus", "utilitarios": "Carro / utilitário"}


def _mega_tipo(link: str) -> str:
    partes = urlsplit(link).path.strip("/").split("/")
    seg = partes[1] if len(partes) > 2 else ""
    return MEGA_TIPOS.get(seg, seg.replace("-", " ").capitalize())


def parse_mega_lista(html: str, categoria: str):
    s = soup(html)
    tot_pag = 1
    if m := re.search(r"P[áa]gina\s+\d+\s+de\s+(\d+)", txt(s.select_one(".summary")) or s.get_text(" ")):
        tot_pag = int(m[1])
    out = []
    for card in s.select("div.card"):
        a = card.select_one("a.card-title")
        if a is None:
            continue
        link = (a.get("href") or "").split("?")[0]
        if link.startswith("/"):
            link = MEGA + link
        img = card.select_one("a.card-image")
        foto = ""
        if img is not None:
            foto = img.get("data-bg") or ""
            if not foto and (mm := re.search(r"url\(['\"]?([^'\")]+)", img.get("style", ""))):
                foto = mm[1]
        inst_tit = card.select_one(".card-instance-title")
        lote_n = txt(card.select_one(".card-batch-number"))
        n_pracas = txt(card.select_one(".card-instances"))
        tipo_leilao = ""
        if inst_tit is not None:
            for x in inst_tit.select(".card-batch-number, .card-instances"):
                x.extract()
            tipo_leilao = txt(inst_tit)
        local = txt(card.select_one(".card-locality"))
        cidade = local.split(",")[0].strip()
        uf_card = re.split(r"[,/-]", local)[-1].strip().upper() if re.search(r"[,/-]", local) else ""
        l = Lote(fonte="Mega Leilões", categoria=categoria, modalidade=_mega_modalidade(tipo_leilao),
                 modalidade_original=f"Mega Leilões – {tipo_leilao or 'Leilão'} ({n_pracas or '—'})",
                 titulo=txt(a), link=link, id_fonte=txt(card.select_one(".card-number")), cidade=cidade,
                 tipo=_mega_tipo(link), uf=uf_card if uf_card in UFS else ("" if nacional() else "MG"),
                 status=txt(card.select_one(".card-status")).capitalize())
        l.foto(foto)
        banco = card.select_one(".card-bank img")
        if banco is not None and banco.get("src"):
            l.det("Comitente (logo)", _logo_para_nome(banco["src"]))
        vb = [txt(x) for x in card.select(".card-views-bids span")]
        if vb:
            l.det("Visitas", vb[0])
        if len(vb) > 1:
            l.det("Lances", vb[1])
        l.det("Lote", lote_n)
        l.det("Praças", n_pracas)
        for inst in card.select(".instance"):
            de = inst.select_one(".card-first-instance-date, .card-second-instance-date")
            t = txt(de)
            mm = re.match(r"(.*?):\s*(\d{2}/\d{2}/\d{4}.*)$", t)
            nome = mm[1] if mm else "Data"
            nome = "Praça única" if nome.lower() == "data" else nome
            l.praca(nome, mm[2] if mm else t, num_br(txt(inst.select_one(".card-instance-value"))))
        if not l.pracas:
            l.valor_minimo = num_br(txt(card.select_one(".card-price")))
        if card.select_one(".is_vacant") is not None:
            l.selo("Desocupado")
            l.det("Ocupação", "Desocupado")
        out.append(l)
    return out, tot_pag


def parse_mega_detalhe(html: str, l: Lote):
    s = soup(html)
    tipo = txt(s.select_one(".batch-type"))
    if tipo:
        l.modalidade = _mega_modalidade(tipo)
    l.det("Leilão", txt(s.select_one(".auction-id")).replace("Leilão", "").strip())
    for sel, nome in ((".views-count", "Visitas"), (".bids-count", "Lances"), (".enabled-count", "Habilitados")):
        v = re.sub(r"[^\d.]", "", txt(s.select_one(sel)))
        if v:
            l.det(nome, v)
    loc = txt(s.select_one(".locality")).replace("Localização", "").strip()
    if loc:
        l.endereco = loc
    autor = s.select_one(".author")
    if autor is not None:
        linhas = [limpa(x) for x in autor.get_text("\n").splitlines() if limpa(x)]
        if linhas:
            l.leiloeiro = " – ".join(linhas[:2])
    desc = s.select_one("#tab-description")
    if desc is not None:
        l.descricao = html_para_texto(str(desc))
        if m := RE_CNJ.search(l.descricao):
            l.processo = m[0]
    l.det("Condições de pagamento", txt(s.select_one("#tab-payment-condition")))
    resumo = s.select_one(".summary-info")
    if resumo is not None:
        tr = txt(resumo)
        if m := re.search(r"In[íi]cio:\s*(\d{2}/\d{2}/\d{4}(?: às \d{2}:\d{2})?)", tr):
            l.det("Início dos lances", m[1])
        novas = []
        for inst in resumo.select(".instance"):
            de = inst.select_one(".card-first-instance-date, .card-second-instance-date")
            mm = re.match(r"(.*?):\s*(\d{2}/\d{2}/\d{4}.*)$", txt(de))
            if mm:
                nome = "Praça única" if mm[1].lower() == "data" else mm[1]
                novas.append((nome, mm[2], num_br(txt(inst.select_one(".card-instance-value")))))
        if novas:
            l.pracas = []
            for n, d, v in novas:
                l.praca(n, d, v)
    ult = txt(s.select_one(".last-bid"))
    if (v := num_br(ult)) and "R$" in ult:
        l.lance_atual = v
    l.det("Incremento", txt(s.select_one(".increment")).replace("Incremento", "").strip())
    for a in s.select(".downloads a"):
        href = a.get("href") or ""
        if "login" not in href:
            l.doc(txt(a), href)
    for img in s.select(".owl-carousel img"):
        l.foto(img.get("src") or img.get("data-src") or "")
    texto = s.get_text(" ")
    if (m := re.search(r"Valor de Avalia[çc][ãa]o\s*R\$\s*([\d.,]+)", texto)) and num_br(m[1]):
        l.valor_avaliacao = num_br(m[1])


class Mega(Fonte):
    chave, nome, site, categorias = "mega", "Mega Leilões", MEGA, (IMOVEIS, VEICULOS)

    def coletar(self):
        lotes, vistos = [], set()
        suf = "" if nacional() else "/mg"
        for categoria, caminho in ((IMOVEIS, "/imoveis" + suf), (VEICULOS, "/veiculos" + suf)):
            pag = 1
            while pag <= 200:
                r = self.http.get(MEGA + caminho, params={"pagina": pag} if pag > 1 else None)
                novos, tot = parse_mega_lista(decodifica(r), categoria)
                novos = [l for l in novos if l.link not in vistos]
                vistos.update(l.link for l in novos)
                lotes += novos
                if pag >= tot or not novos:
                    break
                pag += 1
        log.info("[%s] %d lotes", self.nome, len(lotes))
        self.detalhar(lotes, lambda l: parse_mega_detalhe(decodifica(self.http.get(l.link)), l))
        return lotes


# ======================================================================================
# 4) SUPERBID EXCHANGE / SOLD — imóveis e veículos (API JSON)
# ======================================================================================
SBX_API = "https://offer-query.superbid.net/seo/offers/"
SBX_CATEGORIAS = ((IMOVEIS, "imoveis"), (VEICULOS, "carros-motos"), (VEICULOS, "caminhoes-onibus"))
SBX_CUSTOM = {"vehicleFipeCode": "Código FIPE", "vehicleDebts": "Débitos do veículo",
              "vehicleRestrictions": "Restrições do veículo", "vehicleSearchDate": "Data da consulta veicular",
              "vehicleSearchSource": "Fonte da consulta veicular", "vehicleSearchType": "Tipo de consulta veicular"}


def _sbx_modalidade(modalidade: str, subs: list[str]) -> str:
    k = sem_acento(modalidade or "").lower()
    subs_k = [sem_acento(x).lower() for x in subs]
    if "judicial" in subs_k:
        return M_JUDICIAL
    if "tomada" in k or "proposta" in k:
        return M_TOMADA
    if "venda direta" in k or "balcao" in k or "compre" in k:
        return M_VDIRETA
    if "gov" in subs_k:
        return M_GOV
    return M_EXTRA


def parse_superbid_offer(o: dict, categoria: str) -> Lote:
    p = o.get("product") or {}
    a = o.get("auction") or {}
    od = o.get("offerDetail") or {}
    titulo = limpa(p.get("shortDesc") or a.get("desc") or f"Oferta {o.get('id')}").replace(" ¿ ", " – ")
    subs = [s.get("subMarketplaceDesc", "") for s in a.get("subMarketplaces") or [] if s.get("subMarketplaceDesc")]
    modalidade = a.get("modalityDesc") or "Leilão"
    sub = p.get("subCategory") or {}
    loc = p.get("location") or {}
    cidade = (loc.get("city") or "").rsplit(" - ", 1)[0].strip()
    uf_sbx = uf_por_nome(loc.get("state") or "") or uf_por_nome((loc.get("city") or "").rpartition(" - ")[2])
    l = Lote(uf=uf_sbx or ("" if nacional() else "MG"),
        fonte="Superbid", categoria=categoria, modalidade=_sbx_modalidade(modalidade, subs),
        modalidade_original=" / ".join(x for x in ["Superbid – " + modalidade, "+".join(subs),
                                                   a.get("judicialPracaDescription") or ""] if x),
        titulo=titulo, link=f"https://exchange.superbid.net/oferta/{slugify(titulo)}-{o.get('id')}",
        id_fonte=str(o.get("id", "")), tipo=sub.get("description") or "", cidade=cidade,
        descricao=html_para_texto(p.get("detailedDescription")
                                  or (o.get("offerDescription") or {}).get("offerDescription") or ""),
        valor_minimo=od.get("initialBidValue") or o.get("price"),
        lance_atual=(od.get("currentMaxBid") if o.get("hasBids") else None),
        data_encerramento=fmt_data(parse_data(o.get("endDate"))),
        leiloeiro=limpa(a.get("auctioneer") or "") + (f" (matr. {a['registry']})" if a.get("registry") else ""),
        comitente=limpa((o.get("seller") or {}).get("name") or ""),
    )
    est = o.get("offerStatus") or {}
    l.status = ("Vendido" if est.get("sold") else "Encerrado" if est.get("closed") else
                "Aberto para lances" if est.get("giveYourBid") else
                "Recebendo propostas" if est.get("makeYourProposal") else "")
    etapas = ((a.get("eventPipeline") or o.get("eventPipeline") or {}).get("stages")) or []
    if len(etapas) > 1:
        for i, st in enumerate(etapas, 1):
            l.praca(f"{i}ª praça", st.get("endDate"), st.get("initialBidValue"))
    l.det("Próximo lance mínimo", brl(od.get("currentMinBid")) if od.get("currentMinBid") else None)
    if (res := od.get("reservedPrice")) and res < 1e11 and res != l.valor_minimo:
        l.det("Valor de reserva / referência", brl(res))
    jud = p.get("judicial") or {}
    l.processo = jud.get("processNumber") or ""
    if not l.processo and (m := RE_CNJ.search(l.descricao)):
        l.processo = m[0]
    m = re.search(r"AVALIA[ÇC][ÃA]O[^:\n]{0,30}:\s*R\$\s*([\d.,]+)", l.descricao, re.I)
    if m and "ALQUEIRE" not in m[0].upper():
        l.valor_avaliacao = num_br(m[1])

    l.det("Leilão", a.get("desc"))
    l.det("Loja / leiloeiro na Superbid", (o.get("store") or {}).get("name"))
    l.det("Lote nº", o.get("lotNumber"))
    l.det("Praça", a.get("judicialPracaDescription"))
    l.det("Categoria", (sub.get("category") or {}).get("description"))
    l.det("Subcategoria", sub.get("description"))
    l.det("Marca", (p.get("brand") or {}).get("description"))
    l.det("Modelo", (p.get("model") or {}).get("description"))
    l.det("Início do leilão", fmt_data(parse_data(a.get("beginDate"))))
    l.det("Fim do leilão", fmt_data(parse_data(a.get("endDate"))))
    l.det("Visitas", o.get("visits"))
    l.det("Lances", o.get("totalBids"))
    l.det("Participantes", o.get("totalBidders"))
    l.det("Incremento", (o.get("currentBidIncrement") or {}).get("currentBidIncrementFormatted"))
    go = o.get("groupOffer") or {}
    if go.get("commissionPercent"):
        l.det("Comissão do leiloeiro", f"{fmt_num(go['commissionPercent'], 0)}%")
    if "acceptProposal" in go:
        l.det("Aceita proposta", bool(go.get("acceptProposal")))
    cc = o.get("commercialCondition") or {}
    if cc.get("allowsCreditCard"):
        l.det("Aceita cartão de crédito", True)
    if cc.get("maxInstallments"):
        l.det("Parcelas (máx.)", cc["maxInstallments"])
    for item in o.get("paymentItems") or []:
        nome = (item.get("mapDescriptionTranslator") or {}).get("pt_BR") or "Despesa"
        if item.get("value"):
            l.det(f"Despesa: {nome}", brl(item["value"]))
    for k, nome in (("nature", "Natureza da ação"), ("foro", "Foro"), ("district", "Comarca"),
                    ("author", "Exequente / autor"), ("defendant", "Executado / réu")):
        l.det(nome, jud.get(k))
    for g in ((p.get("template") or {}).get("groups") or []):
        for prop in g.get("properties") or []:
            v = limpa(str(prop.get("value") or "")).strip("\\t ")
            if v and prop.get("title"):
                l.det(prop["title"], v)
    try:
        custom = json.loads(p.get("productCustomJson") or "{}") or {}
    except ValueError:
        custom = {}
    for k, nome in SBX_CUSTOM.items():
        l.det(nome, custom.get(k))
    geo = loc.get("locationGeo") or {}
    if geo.get("lat") and geo.get("lon"):
        l.det("Coordenadas", f"{geo['lat']}, {geo['lon']}")
        l.doc("Ver no mapa", f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}")
    for g in p.get("galleryJson") or []:
        if (g.get("type") or "photo") == "photo":
            l.foto(g.get("link") or g.get("thumbnailUrl") or "")
    if not l.fotos:
        l.foto(p.get("thumbnailUrl") or "")
    if p.get("photoCount") and p["photoCount"] > len(l.fotos):
        l.det("Fotos no site", p["photoCount"])
    if (link := (a.get("internalParameters") or {}).get("highlightUrlLink")):
        l.doc("Contato / destaque do leilão", link.split("?")[0])
    return l


class Superbid(Fonte):
    chave, nome, site, categorias = "superbid", "Superbid Exchange / Sold", "https://www.superbid.net", (IMOVEIS, VEICULOS)

    def coletar(self):
        lotes, vistos = [], set()
        for categoria, slug in SBX_CATEGORIAS:
            pag, lidos = 1, 0
            while pag <= 100:
                params = {"locale": "pt_BR", "portalId": "[2,15]", "requestOrigin": "marketplace",
                          "timeZoneId": "America/Sao_Paulo", "searchType": "opened", "orderBy": "endDate:asc",
                          "pageNumber": pag, "pageSize": 50,
                          "urlSeo": f"https://www.superbid.net/categorias/{slug}"}
                if not nacional():
                    params["filter"] = "product.location.state:Minas Gerais"
                url = SBX_API + "?" + urlencode(params, quote_via=quote, safe="[],:/")
                r = self.http.get(url, aceita404=True, headers={
                    "Accept": "application/json", "Origin": "https://www.superbid.net",
                    "Referer": f"https://www.superbid.net/categorias/{slug}"})
                if r.status_code == 404:
                    break
                j = r.json()
                ofertas = j.get("offers") or []
                for o in ofertas:
                    estado = ((o.get("product") or {}).get("location") or {}).get("state", "")
                    if estado and not aceita_uf(uf_por_nome(estado), vazio=True):
                        continue
                    if o.get("id") in vistos:
                        continue
                    vistos.add(o.get("id"))
                    try:
                        lotes.append(parse_superbid_offer(o, categoria))
                    except Exception as e:  # noqa: BLE001
                        log.debug("Superbid: oferta %s ignorada: %s", o.get("id"), e)
                lidos += len(ofertas)
                if not ofertas or lidos >= int(j.get("total") or 0):
                    break
                pag += 1
        log.info("[%s] %d lotes", self.nome, len(lotes))
        return lotes


# ======================================================================================
# 5) DETRAN-MG — veículos
# ======================================================================================
DETRAN = "https://leilao.detran.mg.gov.br"


def parse_detran_editais(html: str) -> list[dict]:
    s = soup(html)
    eds, vistos = [], set()
    for a in s.select('a[href*="/lotes/lista-lotes/"]'):
        m = re.search(r"/lotes/lista-lotes/(\d+)/(\d+)", a.get("href", ""))
        if not m or m.groups() in vistos:
            continue
        vistos.add(m.groups())
        card = a.find_parent(class_="card") or a.parent
        tc = txt(card)
        mn = re.search(r"Edital de Leil[ãa]o\s*([\d/]+)", txt(card.select_one(".card-title")) or tc, re.I)
        enc = re.search(r"Encerramento:\s*(\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?)", tc)
        patio = ""
        for b in card.select(".card-body b"):
            t = txt(b)
            if re.match(r"\d+\s*-\s*\S", t):
                patio = re.sub(r"^\d+\s*-\s*", "", t)
        status = ""
        st_el = card.select_one(".text-primary")
        if st_el is not None:
            status = txt(st_el)
        eds.append({"id": m[1], "ano": m[2], "numero": mn[1] if mn else m[1],
                    "municipio": titulo_pt(txt(card.select_one(".capa-municipio")).upper()),
                    "patio": patio, "status": status, "encerramento": enc[1] if enc else "",
                    "url": f"{DETRAN}/lotes/lista-lotes/{m[1]}/{m[2]}"})
    return eds


def parse_detran_lotes(html: str, ed: dict):
    s = soup(html)
    paginas = [int(x) for x in re.findall(r"[?&]page=(\d+)", html)]
    tot = max(paginas) if paginas else 1
    out = []
    for card in s.select("div.listaLotes"):
        el = card.select_one('[id^="valor_atual_lote_"]') or card.select_one('[id^="relogio_"]')
        lote_id = el["id"].rsplit("_", 1)[-1] if el is not None else ""
        if not lote_id and (m := re.search(r"/lotes/detalhes/(\d+)", str(card))):
            lote_id = m[1]
        cab = txt(card.select_one(".card-body .row b"))
        mc = re.match(r"Lote\s+(\d+)\s*-\s*(.+)", cab)
        numero, condicao = (mc[1], mc[2].strip()) if mc else ("", "")
        desc = ""
        for b in card.select("div.text-center b"):
            t = txt(b)
            if t and not t.lower().startswith("lote"):
                desc = t
                break
        mano = re.match(r"(.*?)\s+(\d{4})$", desc)
        modelo, ano = (mano[1], mano[2]) if mano else (desc, "")
        valor = num_br(txt(card.select_one('[id^="valor_atual_lote_"]')))
        l = Lote(fonte="Detran-MG", categoria=VEICULOS, modalidade=M_DETRAN,
                 modalidade_original=f"Detran-MG – Edital {ed['numero']}",
                 titulo=desc or f"Lote {numero}", link=f"{DETRAN}/lotes/detalhes/{lote_id}", id_fonte=lote_id,
                 tipo=tipo_veiculo(desc), cidade=ed["municipio"], endereco=ed["patio"], valor_minimo=valor,
                 data_encerramento=fmt_data(parse_data(ed["encerramento"])), comitente="Detran-MG",
                 status=ed.get("status", ""))
        if condicao:
            l.selo(condicao.capitalize())
            l.det("Condição", condicao.capitalize())
        l.det("Edital", ed["numero"])
        l.det("Pátio", ed["patio"])
        l.det("Lote nº", numero)
        l.det("Marca/Modelo", modelo)
        l.det("Ano", ano)
        img = card.select_one("img.card-img-top")
        if img is not None:
            l.foto(urljoin(DETRAN + "/", img.get("src", "").replace("/../", "/")))
        l.doc("Edital (PDF)", f"{DETRAN}/documentos-leiloes/edital/{ed['id']}/{ed['ano']}")
        l.doc("Todos os lotes deste edital", ed["url"])
        out.append(l)
    return out, tot


def parse_detran_detalhe(html: str, l: Lote):
    s = soup(html)
    for dl in s.select("dl"):
        for dt in dl.select("dt"):
            dd = dt.find_next_sibling("dd")
            k, v = txt(dt).rstrip(":"), txt(dd)
            if not k:
                continue
            if k.lower().startswith("valor inicial"):
                l.valor_minimo = num_br(v) or l.valor_minimo
            l.det(k, v)
    if (mm := l.detalhes.get("Marca/Modelo")) and (ano := l.detalhes.get("Ano do Modelo")):
        l.titulo = f"{mm} {ano}"
    for img in s.select("img"):
        src = img.get("src") or ""
        if "img_" in src:
            l.foto(urljoin(DETRAN + "/", src.replace("/../", "/")))


class Detran(Fonte):
    chave, nome, site, categorias = "detran", "Detran-MG", DETRAN, (VEICULOS,)

    def coletar(self):
        home = decodifica(self.http.get(DETRAN + "/"))
        eds = parse_detran_editais(home)
        paginas = [int(x) for x in re.findall(r"[?&]page=(\d+)", home)]
        for pg in range(2, min(max(paginas, default=1), 50) + 1):  # caso a lista de editais seja paginada
            for ed in parse_detran_editais(decodifica(self.http.get(DETRAN + "/", params={"page": pg}))):
                if all((ed["id"], ed["ano"]) != (e["id"], e["ano"]) for e in eds):
                    eds.append(ed)
        log.info("[%s] %d editais abertos", self.nome, len(eds))
        if not eds:
            raise FonteErro("nenhum edital encontrado na página inicial do Detran-MG")

        def por_edital(ed):
            out, pag, tot = [], 1, 1
            while pag <= tot and pag <= 100:
                r = self.http.get(ed["url"], params={"page": pag} if pag > 1 else None)
                lotes, tot = parse_detran_lotes(decodifica(r), ed)
                if not lotes:
                    break
                out += lotes
                pag += 1
            return out

        lotes, falhas = [], 0
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.workers)) as ex:
            for fut in as_completed([ex.submit(por_edital, ed) for ed in eds]):
                try:
                    lotes += fut.result()
                except Exception as e:  # noqa: BLE001
                    falhas += 1
                    log.debug("Detran: edital falhou: %s", e)
        if falhas:
            self.avisos.append(f"{falhas} editais não puderam ser lidos")
        log.info("[%s] %d veículos", self.nome, len(lotes))
        self.detalhar(lotes, lambda l: parse_detran_detalhe(decodifica(self.http.get(l.link)), l))
        return lotes


# ======================================================================================
# 6) PALÁCIO DOS LEILÕES — veículos (pátio de Juatuba/MG)
# ======================================================================================
PALACIO = "https://www.palaciodosleiloes.com.br/site/"
PALACIO_AJAX = PALACIO + "camada_ajax/coluna_esquerda_m_home.php"
PALACIO_ENDERECOS = {"juatuba": "BR-262, km 375, Juatuba/MG"}


def _palacio_opcoes(html: str) -> list[tuple[str, str]]:
    s = soup(html)
    out = []
    for inp in s.select("input[type=checkbox]"):
        pai = inp.find_parent(["tr", "li", "label", "div"])
        out.append((inp.get("value", ""), txt(pai)))
    return out


def parse_palacio_lista(html: str) -> list[Lote]:
    s = soup(html)
    out = []
    for card in s.select("article.resultado-lote-card"):
        alvo = card.select_one('[onclick*="exibir_lote("]')
        m = re.search(r"exibir_lote\((\d+)\s*,\s*(\d+)\)", alvo.get("onclick", "")) if alvo else None
        if not m:
            continue
        cod_lote, cod_leilao = m[1], m[2]
        ids = [txt(x) for x in card.select(".resultado-veiculo-identificadores span")]
        num_lote = next((re.sub(r"\D", "", x) for x in ids if x.lower().startswith("lote")), "")
        seq = next((re.sub(r"\D", "", x) for x in ids if x.lower().startswith("seq")), "")
        modelo = txt(card.select_one(".resultado-veiculo-modelo"))
        situacao = txt(card.select_one(".resultado-veiculo-situacao"))
        comitente = txt(card.select_one(".resultado-veiculo-comitente span"))
        dados = [txt(x) for x in card.select(".resultado-veiculo-dados span")]
        data = next((x for x in dados if RE_DATA.search(x)), "")
        local = dados[2] if len(dados) > 2 else ""
        stats = [re.sub(r"\D", "", txt(x)) for x in card.select(".resultado-veiculo-estatisticas span")]
        caixa_valor = card.select_one(".resultado-veiculo-valor")
        rot_valor = txt(caixa_valor.select_one("span")) if caixa_valor else ""
        valor = num_br(txt(caixa_valor.select_one("strong"))) if caixa_valor else None
        l = Lote(fonte="Palácio dos Leilões", categoria=VEICULOS, modalidade=M_EXTRA,
                 modalidade_original=f"Palácio dos Leilões – leilão {cod_leilao}",
                 titulo=modelo or f"Lote {num_lote}", link=f"{PALACIO}lotem.php?cl={cod_lote}", id_fonte=cod_lote,
                 tipo=tipo_veiculo(modelo), cidade=local if local.lower() != "externo" else "",
                 endereco=PALACIO_ENDERECOS.get(local.lower(), "Fora do pátio (ver edital)" if local.lower() == "externo" else local),
                 comitente=comitente, data_encerramento=fmt_data(parse_data(data)))
        if "inicial" in rot_valor.lower():
            l.valor_minimo = valor
        else:
            l.lance_atual = valor
        if situacao:
            l.det("Situação do veículo", situacao)
            if "estado que se encontra" not in situacao.lower():
                l.selo(situacao.capitalize())
        l.det("Leilão nº", cod_leilao)
        l.det("Data do leilão", fmt_data(parse_data(data)))
        l.det("Sequência", seq)
        l.det("Lote nº", num_lote)
        l.det("Local", local)
        if stats:
            l.det("Visualizações", stats[0])
        if len(stats) > 1:
            l.det("Lances", stats[1])
        l.det(rot_valor or "Valor", brl(valor) if valor else None)
        l._palacio = (cod_lote, cod_leilao, num_lote)  # usado no detalhe
        img = card.select_one(".resultado-veiculo-foto img") or card.select_one("img")
        if img is not None:
            l.foto(urljoin(PALACIO, img.get("src", "")))
        out.append(l)
    return out


def parse_palacio_detalhe(html: str, l: Lote):
    s = soup(html)
    for inp_id, nome, fmt in (("val_despesa", "Despesas administrativas", "brl"),
                              ("val_comissao", "Comissão do leiloeiro", "pct")):
        el = s.find("input", id=inp_id)
        if el is not None and (v := num_br(el.get("value"))):
            l.det(nome, brl(v) if fmt == "brl" else f"{fmt_num(v, 0)}%")
    el = s.find("input", id="lance_inicial")
    if el is not None and (v := num_br(el.get("value"))):
        l.valor_minimo = v
    tabela = None
    for t in s.find_all("table"):
        if re.search(r"Informa..es do bem", t.get_text()):
            tabela = t
            break
    if tabela is not None:
        visto_lote = False
        for tr in tabela.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) != 2:
                continue
            k, v = txt(tds[0]), txt(tds[1])
            if k.lower() == "lote":
                if visto_lote:
                    k = "Endereço do lote"
                visto_lote = True
            if k.lower() == "origem" and v:
                l.modalidade_original = f"{l.modalidade_original} – origem: {v}"
            l.det(k, v)
    cab = s.find(lambda t: t.name == "div" and "Descri" in t.get_text() and "detalhada" in t.get_text()
                 and len(t.get_text()) < 60)
    if cab is not None:
        corpo = cab.find_next_sibling("div")
        if corpo is not None:
            l.descricao = html_para_texto(str(corpo))
    itens = []
    for i in s.select("i.fa-check"):
        t = txt(i.parent)
        if t and t not in itens:
            itens.append(t)
    if itens:
        l.det("Checklist", ", ".join(itens))
    lances = len(s.find_all("td", string=re.compile(r"^\s*LANCE\s*$")))
    if lances:
        l.det("Lances registrados", lances)
    for img in s.select(".carousel-item img"):
        l.foto(urljoin(PALACIO, img.get("src", "")))


class Palacio(Fonte):
    chave, nome, site, categorias = "palacio", "Palácio dos Leilões", PALACIO, (VEICULOS,)

    def _ids(self, opcao: str, padrao: str) -> list[str]:
        try:
            r = self.http.post(PALACIO_AJAX, data={"opcao": opcao})
            return [v for v, rot in _palacio_opcoes(decodifica(r)) if v and re.search(padrao, sem_acento(rot), re.I)]
        except Exception:  # noqa: BLE001
            return []

    def coletar(self):
        estados = self._ids("carregar_estado", r"." if nacional() else r"minas") or ["1"]
        cats = self._ids("carregar_categoria", r"autom|veic|moto|caminh|onibus|utilit|pesad") or ["1"]
        corpo = {"opcao": "listar_lote", "categoria_pesquisa": ",".join(cats),
                 "estado_site_pesquisa": ",".join(estados), "cidade_site_pesquisa": "",
                 "grupo_site_pesquisa": "", "subcategoria_pesquisa": "", "marca_pesquisa": "",
                 "modelo_pesquisa": "", "ano_pesquisa": "", "situacao_pesquisa": "", "local_pesquisa": "",
                 "leilao_pesquisa": "", "txt_pesquisa_lote": "", "tipo_exibicao": "grid", "paginacao": "-1",
                 "total_paginas": "1", "somente_pesquisa": "0", "e_categoria": "1", "e_estado": "1",
                 "e_leilao": "0", "e_subcategoria": "0", "e_marca": "0", "e_modelo": "0", "e_ano": "0",
                 "e_situacao": "0", "e_local": "0", "e_grupo": "0", "e_cidade": "0"}
        r = self.http.post(PALACIO_AJAX, data=corpo, headers={"Referer": PALACIO + "index.php",
                                                               "X-Requested-With": "XMLHttpRequest"})
        lotes = parse_palacio_lista(decodifica(r))
        log.info("[%s] %d veículos", self.nome, len(lotes))

        def det(l: Lote):
            cod_lote, cod_leilao, num_lote = l._palacio
            r = self.http.post(PALACIO + "camada_ajax/lotem.php", data={
                "opcao": "exibir_lote_m", "cod_lote": cod_lote, "cod_leilao": cod_leilao, "num_lote": num_lote},
                headers={"Referer": l.link, "X-Requested-With": "XMLHttpRequest"})
            parse_palacio_detalhe(decodifica(r), l)

        self.detalhar(lotes, det)
        return lotes


# ======================================================================================
# 7) FRANCO LEILÕES — imóveis (API JSON da plataforma)
# ======================================================================================
FRANCO = "https://www.francoleiloes.com.br"
FRANCO_ENCERRADOS = {4, 6, 7}  # 4 = suspenso, 6 = encerrado, 7 = arrematado/finalizado
_RE_FIM = re.compile(r"encerr|suspens|arremat|vendid|cancel|retirad|prejudicad|finaliz", re.I)
_RE_CAT_VEIC = re.compile(r"ve[ií]cul|carro|\bmoto|caminh|[ôo]nibus|autom|utilit", re.I)
_RE_CAT_IMOV = re.compile(r"im[óo]v|residenc|terreno|comerc|rura|apart|casa|galp|sala|ch[áa]cara|fazenda|gleba", re.I)
_PRACAS_FRANCO = (("Primeira", "1ª praça"), ("Segunda", "2ª praça"), ("Terceira", "3ª praça"))


def _franco_payload(pagina: int) -> dict:
    return {"RangeValores": 0, "Scopo": 1, "IgnoreScopo": 0, "OrientacaoBusca": 1, "Mapa": "", "Busca": "",
            "ID_Categoria": 0, "ID_Estado": 31, "ID_Cidade": 0, "Bairro": "", "ID_Regiao": 0,
            "ValorMinSelecionado": 0, "ValorMaxSelecionado": 0, "CFGs": "", "sInL": "", "Pagina": pagina,
            "Ordem": 0, "OrdSt": 0, "QtdPorPagina": 500, "SubStatus": [], "ID_Leiloes_Status": [],
            "BuscaProcesso": "", "NomesPartes": "", "CodLeilao": "", "TiposLeiloes": [], "PaginaIndex": 1}


def parse_franco_lote(x: dict) -> Lote | None:
    if (x.get("ID_Leiloes_Status") or 0) in FRANCO_ENCERRADOS:
        return None
    rt = (x.get("GetLoteRealTime") or [{}])[0] or {}
    if _RE_FIM.search(rt.get("Lote_SubStatus_Label") or ""):
        return None
    if not nacional() and not aceita_uf(x.get("UF") or "MG") and x.get("Lote_ID_Estado") != 31:
        return None
    cat_txt = f"{x.get('Categoria') or ''} {x.get('IconeCategoria') or ''}"
    categoria = VEICULOS if _RE_CAT_VEIC.search(cat_txt) else IMOVEIS if _RE_CAT_IMOV.search(cat_txt) else None
    if not categoria:
        return None
    rotulo = limpa(x.get("LabelModalidade"))
    if rt.get("IsVendaDireta"):
        modalidade = M_VDIRETA
    elif chave(rotulo) == "judicial" or (not rotulo and RE_JUDICIAL.search(x.get("Comitente") or "")):
        modalidade = M_JUDICIAL
    else:
        modalidade = M_EXTRA
    titulo = limpa(x.get("Lote")) or f"Lote {x.get('ID_Leiloes_Lote')}"
    l = Lote(fonte="Franco Leilões", categoria=categoria, modalidade=modalidade, titulo=titulo,
             link=urljoin(FRANCO + "/", x.get("URLlote") or ""), id_fonte=str(x.get("ID_Leiloes_Lote") or ""),
             modalidade_original=" / ".join(v for v in ("Franco – " + (rotulo or "Leilão"), limpa(x.get("Leilao"))) if v),
             cidade=limpa(x.get("Cidade")), bairro=limpa(x.get("Lote_Bairro")), uf=(x.get("UF") or "MG").upper(),
             comitente=limpa(x.get("Comitente")), status=limpa(rt.get("Lote_SubStatus_Label") or rt.get("StatusLote")))
    if categoria == IMOVEIS:
        l.tipo = tipo_imovel(titulo, {"Comerciais": "Comercial", "Rurais": "Rural"}.get(x.get("IconeCategoria"), "Imóvel"))
    else:
        l.tipo = tipo_veiculo(titulo)
    rua = ", ".join(limpa(v) for v in (x.get("Lote_Endereco"), x.get("Lote_Numero"), x.get("Lote_Complemento")) if limpa(v))
    l.endereco = " – ".join(v for v in (rua, l.bairro, f"{l.cidade}/{l.uf}" if l.cidade else "",
                                         f"CEP {x['Lote_CEP']}" if x.get("Lote_CEP") else "") if v)
    qtd = rt.get("QtdPracas") or 1
    for i, (nome, padrao) in enumerate(_PRACAS_FRANCO[:qtd]):
        data = rt.get(f"DataHoraEncerramento{nome}Praca") or ""
        valor = rt.get(f"ValorMinimoLance{nome}Praca") or None
        if data.startswith("1900"):
            data = ""
        if data or valor:
            l.praca(limpa(rt.get(f"Label{nome}Praca")) or padrao, data or None, valor)
    for v in (x.get("ValorAvaliacao"), rt.get("ValorAvaliacao"), x.get("GetValorAvaliacao")):
        if v:
            l.valor_avaliacao = float(v)
            break
    if rt.get("ValorLanceAtual"):
        l.lance_atual = float(rt["ValorLanceAtual"])
    l.data_encerramento = fmt_data(parse_data(rt.get("DataTermino")))
    l.processo = limpa(x.get("NumeroProcesso"))
    for c in x.get("CFGForms") or []:
        if chave(c.get("Label") or "") == "processo" and c.get("Value") and not l.processo:
            l.processo = limpa(c["Value"])
    l.det("Lote", x.get("LoteNumero"))
    l.det("Leilão", " – ".join(v for v in (limpa(x.get("CodLeilao")), limpa(x.get("Leilao"))) if v))
    if x.get("Comissao"):
        l.det("Comissão do leiloeiro", f"{fmt_num(x['Comissao'], 0)}%")
    if rt.get("ValorIncremento"):
        l.det("Incremento", brl(rt["ValorIncremento"]))
    for k, nome in (("Visitas", "Visitas"), ("Habilitacoes", "Habilitados"), ("Lances", "Lances"), ("Propostas", "Propostas")):
        if x.get(k):
            l.det(nome, x[k])
    if rt.get("PracaAtual"):
        l.det("Praça atual", f"{rt['PracaAtual']}ª")
    if x.get("IsAceitaFinanciamento"):
        l.det("Aceita financiamento", True)
        l.selo("Aceita financiamento")
    if x.get("IsDesocupado"):
        l.det("Desocupado", True)
        l.selo("Desocupado")
    for k, nome in (("Vara", "Vara"), ("JuizoDa", "Juízo"), ("Foro", "Foro"), ("Autor", "Exequente / autor"),
                    ("Reu", "Executado / réu")):
        l.det(nome, x.get(k))
    l.det("CEP", x.get("Lote_CEP"))
    coord = limpa(x.get("Coordenadas"))
    if re.match(r"^-?\d+\.\d+,\s*-?\d+\.\d+$", coord):
        l.det("Coordenadas", coord)
        l.doc("Ver no mapa", "https://www.google.com/maps?q=" + coord.replace(" ", ""))
    for f in x.get("Fotos") or []:
        if f.get("Foto"):
            l.foto(f"{FRANCO}/imagens/800x600/{f['Foto']}")
    return l


def parse_franco_detalhe(html: str, l: Lote):
    s = soup(html)
    d = s.select_one(".dg-lote-descricao-txt")
    if d is not None:
        for br in d.find_all("br"):
            br.replace_with("\n")
        l.descricao = html_para_texto(str(d))
    if not l.processo and (m := RE_CNJ.search(l.descricao)):
        l.processo = m[0]
    for a in s.select("a[href]"):
        if re.search(r"\.pdf(\?|$)", a.get("href", ""), re.I):
            l.doc(txt(a) or "Documento", urljoin(l.link, a["href"]))


class Franco(Fonte):
    chave, nome, site, categorias = "franco", "Franco Leilões", FRANCO, (IMOVEIS, VEICULOS)

    def coletar(self):
        sessao = self.http.nova_sessao()
        r = self.http.get(FRANCO + "/busca/", sessao=sessao)
        html = decodifica(r)
        m = re.search(r'name="__RequestVerificationToken"[^>]*value="([^"]+)"', html) or \
            re.search(r'value="([^"]+)"[^>]*name="__RequestVerificationToken"', html)
        token = m[1] if m else ((sessao.cookies.get("XSRF-TOKEN") if sessao is not None else "") or "")
        cab = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
               "X-Requested-With": "XMLHttpRequest", "Referer": FRANCO + "/busca/", "Origin": FRANCO}
        if token:
            cab["__RVT"] = token
        lotes, vistos = [], set()
        for pagina in range(1, 21):
            r = self.http.post(f"{FRANCO}/ApiEngine/GetBusca/{pagina}/1/0", sessao=sessao, headers=cab,
                               data=json.dumps(_franco_payload(pagina)))
            if not r.content.strip():
                raise FonteErro("a busca da Franco respondeu vazio (o formato da consulta pode ter mudado)")
            j = r.json()
            itens = j.get("Lotes") or []
            for x in itens:
                if x.get("ID_Leiloes_Lote") in vistos:
                    continue
                vistos.add(x.get("ID_Leiloes_Lote"))
                try:
                    l = parse_franco_lote(x)
                except Exception as e:  # noqa: BLE001
                    log.debug("Franco: lote %s ignorado: %s", x.get("ID_Leiloes_Lote"), e)
                    continue
                if l:
                    lotes.append(l)
            if len(itens) < 500 or pagina >= (j.get("PageIndexMax") or 0):
                break
        log.info("[%s] %d lotes abertos", self.nome, len(lotes))
        self.detalhar(lotes, lambda l: parse_franco_detalhe(decodifica(self.http.get(l.link)), l))
        return lotes


# ======================================================================================
# 8) LEILÕES JUDICIAIS MG (leiloesjudiciaismg.com.br) — imóveis e veículos
# ======================================================================================
LJMG = "https://www.leiloesjudiciaismg.com.br"
LJMG_CATEGORIAS = ((IMOVEIS, "imoveis"), (VEICULOS, "veiculos"))
LJMG_TIPOS = {"apartamentos": "Apartamento", "casas": "Casa", "comerciais": "Comercial",
              "fazendassitiosechacaras": "Rural", "terrenoselotes": "Terreno", "industriais": "Galpão / industrial",
              "vagasdegaragem": "Vaga de garagem", "carros": "Carro / utilitário", "motos": "Moto",
              "caminhoes": "Caminhão / reboque", "veiculospesados": "Caminhão / reboque", "onibus": "Ônibus"}


def _ljmg_busca(dados) -> dict:
    for k, v in ((dados or {}).get("data") or {}).items():
        if k.startswith("busca") and isinstance(v, dict) and isinstance(v.get("dadosGeral"), dict):
            return v["dadosGeral"]
    return {}


def _ljmg_modalidade(texto: str) -> str:
    k = sem_acento(texto or "").lower()
    if "venda direta" in k:
        return M_VDIRETA
    if "extrajudicial" in k:
        return M_EXTRA
    return M_JUDICIAL


def parse_ljmg_item(it: dict, categoria: str) -> Lote | None:
    if _RE_FIM.search(it.get("nm_statuslote") or ""):
        return None
    titulo = limpa(it.get("nm_titulo_lote")) or f"Lote {it.get('lote_id')}"
    sub = limpa(it.get("nm_subcategoria"))
    l = Lote(fonte="Leilões Judiciais MG", categoria=categoria,
             modalidade=_ljmg_modalidade(it.get("nm_titulo_leilao")), titulo=titulo,
             link=f"{LJMG}/lote/{it.get('leilao_id')}/{it.get('lote_id')}", id_fonte=str(it.get("lote_id") or ""),
             modalidade_original="Leilões Judiciais MG", cidade=limpa(it.get("nm_cidade")),
             uf=limpa(it.get("nm_estado")) or "MG", descricao=html_para_texto(it.get("nm_descricao") or ""),
             leiloeiro=limpa(it.get("nm_leiloeiro")), status=limpa(it.get("nm_statuslote")))
    l.tipo = LJMG_TIPOS.get(chave(sub)) or (tipo_imovel(titulo, sub or "Imóvel") if categoria == IMOVEIS
                                            else tipo_veiculo(titulo) if not sub or chave(sub) == "outros" else sub)
    v1, v2, vmin = num_br(it.get("vl_lanceinicial")), num_br(it.get("vl_lanceinicialsegundoleilao")), num_br(it.get("vl_lanceminimo"))
    if l.modalidade == M_VDIRETA:
        l.praca("Venda direta", it.get("dt_fechamento"), v1)
    else:
        l.praca("1º leilão", it.get("dt_fechamento"), v1)
        if v2 and v2 != v1:
            l.praca("2º leilão", None, v2)
    l.valor_minimo = v1
    if it.get("bn_exibeavaliacao") and vmin and v1 and vmin >= v1:
        l.valor_avaliacao = vmin
    if (it.get("nu_qtdelances") or 0) > 0 and num_br(it.get("vl_lance")):
        l.lance_atual = num_br(it.get("vl_lance"))
    if (m := RE_CNJ.search(l.descricao)):
        l.processo = m[0]
    l.det("Lote nº", it.get("nu"))
    l.det("Leilão", it.get("nm_titulo_leilao"))
    l.det("Subcategoria", sub)
    if num_br(it.get("vl_incremento")):
        l.det("Incremento", brl(num_br(it.get("vl_incremento"))))
    if (it.get("nu_parcelas") or 1) > 1:
        l.det("Parcelamento", f"até {it['nu_parcelas']}x, entrada de {fmt_num(it.get('vl_percentualentrada') or 0, 0)}%")
        l.selo("Aceita parcelamento")
    l.det("Visitas", it.get("nu_visitas"))
    l.det("Lances", it.get("nu_qtdelances"))
    if it.get("nm_url_leiloeiro"):
        l.doc("Site do leiloeiro", "https://" + it["nm_url_leiloeiro"].replace("https://", "").replace("http://", ""))
    for e in it.get("usuariovistorialotexstatusliberacaoextra") or []:
        if (e or {}).get("nm"):
            l.selo(e["nm"])
    for f in it.get("fotos") or []:
        u = (f or {}).get("nm_path_completo") or ""
        l.foto(re.sub(r"/fotos/(imoveis|veiculos|equipamentos)/\d+x\d+/", r"/fotos/\1/640x480/", u))
    for a in it.get("anexos") or []:
        l.doc((a or {}).get("nm") or "Anexo", (a or {}).get("nm_path_completo") or "")
    return l


def parse_ljmg_detalhe(html: str, l: Lote):
    dados = nuxt_data(html) or {}
    d = dados.get("data") or {}
    lei = next((v for k, v in d.items() if k.startswith("dadosLeilao-") and isinstance(v, dict)), {}) or {}
    if not lei:
        return
    st = limpa(lei.get("nm_statusleilao") or lei.get("nm_status"))
    tp = lei.get("tp_judicial_extrajudicial")
    if "venda direta" in sem_acento(st).lower():
        l.modalidade = M_VDIRETA
    elif "extrajudicial" in sem_acento(st).lower() or tp == 1:
        l.modalidade = M_EXTRA
    elif "judicial" in sem_acento(st).lower() or tp == 2:
        l.modalidade = M_JUDICIAL
    if st:
        l.modalidade_original = "Leilões Judiciais MG – " + st
    l.comitente = ", ".join(limpa(c.get("nm")) for c in lei.get("comitente") or [] if (c or {}).get("nm"))
    if l.modalidade == M_VDIRETA and l.pracas and l.pracas[0]["nome"] != "Venda direta":
        p0 = l.pracas[0]
        l.pracas = []
        l.praca("Venda direta", datetime.fromisoformat(p0["iso"]) if p0.get("iso") else None, p0.get("valor"))
    elif l.modalidade != M_VDIRETA and lei.get("dt"):
        v1 = l.pracas[0]["valor"] if l.pracas else l.valor_minimo
        v2 = l.pracas[1]["valor"] if len(l.pracas) > 1 else None
        l.pracas = []
        l.praca("1º leilão", lei.get("dt"), v1)
        if lei.get("dt_segundoleilao_data"):
            l.praca("2º leilão", f"{lei['dt_segundoleilao_data']} {lei.get('dt_segundoleilao_hora') or ''}", v2)
        elif v2:
            l.praca("2º leilão", None, v2)
    if lei.get("vl_percentualdepreciacao"):
        l.det("Desconto no 2º leilão", f"{fmt_num(float(lei['vl_percentualdepreciacao']), 0)}% sobre o 1º")
    l.det("Tipo do leilão", st)
    if (lei.get("nu_qtdelotes") or 0) > 1:
        l.det("Lotes no leilão", lei["nu_qtdelotes"])


class LeiloesJudiciaisMG(Fonte):
    chave, nome, site, categorias = "ljmg", "Leilões Judiciais MG", LJMG, (IMOVEIS, VEICULOS)

    def coletar(self):
        lotes, vistos = [], set()
        for categoria, slug in LJMG_CATEGORIAS:
            pagina, total_pag = 1, 1
            while pagina <= min(total_pag, 50):
                filtro = "" if nacional() else "estado=MG"
                qs = "&".join(v for v in (filtro, f"pagina={pagina}" if pagina > 1 else "") if v)
                url = f"{LJMG}/{slug}" + (f"?{qs}" if qs else "")
                dg = _ljmg_busca(nuxt_data(decodifica(self.http.get(url))))
                if not dg:
                    if pagina == 1:
                        raise FonteErro(f"não encontrei a lista de lotes em {url} (o site pode ter mudado)")
                    break
                total_pag = int(dg.get("totalPages") or 1)
                for it in dg.get("items") or []:
                    if not aceita_uf(it.get("nm_estado") or "MG") or it.get("lote_id") in vistos:
                        continue
                    vistos.add(it.get("lote_id"))
                    try:
                        l = parse_ljmg_item(it, categoria)
                    except Exception as e:  # noqa: BLE001
                        log.debug("LJMG: lote %s ignorado: %s", it.get("lote_id"), e)
                        continue
                    if l:
                        lotes.append(l)
                pagina += 1
        log.info("[%s] %d lotes", self.nome, len(lotes))
        self.detalhar(lotes, lambda l: parse_ljmg_detalhe(decodifica(self.http.get(l.link)), l))
        return lotes


# ======================================================================================
# 9) GP LEILÕES (Belo Horizonte) — imóveis e veículos (API JSON)
# ======================================================================================
GP = "https://www.gpleiloes.com.br"
GP_API = GP + "/gp-api"
GP_PADRAO = {IMOVEIS: [6], VEICULOS: [2, 13]}


def _gp_modalidade(leilao: dict) -> str:
    k = sem_acento(f"{leilao.get('sobre') or ''} {leilao.get('titulo') or ''}").lower()
    com = leilao.get("comitente") or leilao.get("nomeLeilao") or ""
    if "extrajudicial" in k:
        return M_EXTRA
    if "judicial" in k or RE_JUDICIAL.search(com):
        return M_JUDICIAL
    if "administracao publica" in k or re.search(r"prefeitura|munic[íi]pio|secretaria|governo|estado de|autarquia|"
                                                 r"c[âa]mara|metr[ôo]|cemig|copasa|pol[íi]cia|departamento", com, re.I):
        return M_GOV
    return M_EXTRA


def parse_gp_lote(x: dict, categoria: str) -> Lote | None:
    if _RE_FIM.search(x.get("situacaoDescricao") or ""):
        return None
    lei = x.get("leilao") or {}
    loc = limpa(x.get("localizacao"))
    cidade, _, uf = loc.rpartition("/")
    uf = (uf or "MG").strip().upper()
    if not aceita_uf(uf):
        return None
    titulo = limpa(x.get("resumo")) or f"Lote {x.get('numeroLote')}"
    n_lei, n_lote = x.get("numeroLeilao") or lei.get("codigoLeilao"), x.get("numeroLote")
    l = Lote(fonte="GP Leilões", categoria=categoria, modalidade=_gp_modalidade(lei), titulo=titulo,
             link=f"{GP}/#/lote/{n_lei}/{n_lote}", id_fonte=str(x.get("id") or ""), cidade=titulo_pt(cidade),
             modalidade_original=" / ".join(v for v in ("GP Leilões", limpa(lei.get("nomeLeilao")), limpa(lei.get("titulo"))) if v),
             descricao=limpa(x.get("bens")), valor_minimo=x.get("valorMinimo") or None, comitente=limpa(lei.get("comitente")),
             data_encerramento=fmt_data(parse_data(x.get("encerramento"))), status=limpa(x.get("situacaoDescricao")),
             leiloeiro="GP Leilões")
    l.tipo = tipo_imovel(f"{titulo} {l.descricao[:200]}") if categoria == IMOVEIS else tipo_veiculo(f"{titulo} {l.descricao[:300]}")
    l._gp = (n_lei, n_lote)
    l.det("Leilão", f"{n_lei} – {limpa(lei.get('titulo'))}")
    l.det("Lote nº", n_lote)
    if x.get("comissao"):
        l.det("Comissão do leiloeiro", f"{fmt_num(x['comissao'], 0)}%")
    if x.get("incremento"):
        l.det("Incremento", brl(x["incremento"]))
    l.det("Visitas", x.get("acessos"))
    l.det("Lances", x.get("lances"))
    l.det("Data do leilão", fmt_data(parse_data(lei.get("dataHora"))))
    if x.get("encerramento2") and lei.get("duaspracas"):
        l.det("Encerramento da 2ª praça", fmt_data(parse_data(x["encerramento2"])))
    l.foto(x.get("capa") or "")
    l.doc("Página do leilão", f"{GP}/#/leilao/{n_lei}")
    return l


def parse_gp_detalhe(v: dict, l: Lote):
    if not v:
        return
    if v.get("descricao"):
        l.descricao = html_para_texto(v["descricao"])
    if v.get("duaspracas"):
        l.pracas = []
        l.praca("1ª praça", v.get("encerramento"), v.get("valorMinimo") or None)
        l.praca("2ª praça", v.get("encerramento2"), v.get("valorMinimo2") or None)
    if v.get("situacaoDescricao"):
        l.status = limpa(v["situacaoDescricao"])
    if v.get("cidade"):
        l.cidade = titulo_pt(v["cidade"])
    if l.categoria == IMOVEIS and v.get("subcategoria") and chave(v["subcategoria"]) not in ("imoveis", "outros"):
        l.tipo = limpa(v["subcategoria"])
    l.det("Subcategoria", v.get("subcategoria"))
    for k, nome in (("condicaoVisitacao", "Visitação"), ("condicaoPagamento", "Pagamento"),
                    ("condicaoRetirada", "Retirada")):
        l.det(nome, html_para_texto(v.get(k) or "").replace("\n", " "))
    end = ", ".join(limpa(v.get(k)) for k in ("visitacaoLogradouro", "visitacaoNumero", "visitacaoBairro") if limpa(v.get(k)))
    if end:
        cid = limpa(v.get("visitacaoCidade"))
        l.endereco = end + (f" – {titulo_pt(cid)}/{v.get('visitacaoUf') or 'MG'}" if cid else "")
    lat, lon = limpa(v.get("latitude")), limpa(v.get("longitude"))
    if lat and lon:
        l.det("Coordenadas", f"{lat}, {lon}")
        l.doc("Ver no mapa", f"https://www.google.com/maps?q={lat},{lon}")
    if limpa(v.get("video")).startswith("http"):
        l.doc("Vídeo", v["video"])
    for img in v.get("imagens") or []:
        l.foto((img or {}).get("src") or "")
    if (m := RE_CNJ.search(l.descricao)):
        l.processo = m[0]


class GPLeiloes(Fonte):
    chave, nome, site, categorias = "gp", "GP Leilões", GP, (IMOVEIS, VEICULOS)

    def _categorias(self) -> dict:
        try:
            cats = self.http.get(GP_API + "/comum/categoria", headers={"Accept": "application/json"}).json().get("value") or []
        except Exception:  # noqa: BLE001
            return GP_PADRAO
        im = [c["id"] for c in cats if re.search(r"im[óo]ve", c.get("nome") or "", re.I)]
        ve = [c["id"] for c in cats if re.search(r"carro|moto|caminh|[ôo]nibus|ve[íi]culos? (leve|pesad)", c.get("nome") or "", re.I)
              and not re.search(r"pe[çc]a", c.get("nome") or "", re.I)]
        return {IMOVEIS: im or GP_PADRAO[IMOVEIS], VEICULOS: ve or GP_PADRAO[VEICULOS]}

    def coletar(self):
        cab = {"Accept": "application/json", "Content-Type": "text/plain;charset=UTF-8",
               "Origin": GP, "Referer": GP + "/"}
        lotes, vistos = [], set()
        for categoria, ids in self._categorias().items():
            pagina, lidos = 1, 0
            while pagina <= 40:
                filtro = {"categorias": ids} if nacional() else {"ufs": sorted(ESCOPO_UFS), "categorias": ids}
                corpo = json.dumps({"filtro": filtro})
                j = self.http.post(f"{GP_API}/index/pesquisar/{pagina}/30", headers=cab, data=corpo).json()
                val = j.get("value") or {}
                itens = ((val.get("content") or {}).get("lotes")) or []
                for x in itens:
                    if x.get("id") in vistos:
                        continue
                    vistos.add(x.get("id"))
                    try:
                        l = parse_gp_lote(x, categoria)
                    except Exception as e:  # noqa: BLE001
                        log.debug("GP: lote %s ignorado: %s", x.get("id"), e)
                        continue
                    if l:
                        lotes.append(l)
                lidos += len(itens)
                if not itens or lidos >= int((val.get("resume") or {}).get("total") or 0):
                    break
                pagina += 1
        log.info("[%s] %d lotes", self.nome, len(lotes))

        def det(l: Lote):
            n_lei, n_lote = l._gp
            j = self.http.get(f"{GP_API}/index/recuperarLote/{n_lei}/{n_lote}", headers={"Accept": "application/json"}).json()
            parse_gp_detalhe(j.get("value") or {}, l)

        self.detalhar(lotes, det)
        return lotes


# ======================================================================================
# 10) DANIEL GARCIA LEILÕES — imóveis e veículos (HTML)
# ======================================================================================
DG = "https://www.danielgarcialeiloes.com.br"
_RE_DG_VEIC = re.compile(r"ve[ií]cul|autom[óo]v|motocicl|\bmoto\b|caminh|camionete|caminhonete|[ôo]nibus|utilit|reboque|carro", re.I)
_RE_DG_IMOV = re.compile(r"im[óo]v|apartament|terreno|\bcasa\b|fra[çc][ãa]o|possess[óo]rio|rural|galp|\bsala\b|"
                         r"ch[áa]cara|s[íi]tio|fazenda|gleba|\bloja\b|pr[ée]dio", re.I)


def _dg_abs(href: str) -> str:
    return urljoin(DG + "/", (href or "").split("?")[0]).replace("://danielgarcialeiloes", "://www.danielgarcialeiloes")


def parse_dg_leilao(html: str, venda_direta: bool = False) -> tuple[dict, list[dict]]:
    s = soup(html)
    h1 = txt(s.find("h1"))
    tags = [txt(h) for h in s.select("h5.m-0")]
    leiloeiro = next((txt(h) for h in s.find_all("h5") if "LEILOEIRO" in txt(h).upper()), "")
    info = {"titulo": h1, "tags": tags, "leiloeiro": re.sub(r"^LEILOEIRO OFICIAL:\s*", "", leiloeiro, flags=re.I),
            "venda_direta": venda_direta}
    itens, vistos = [], set()
    for a in s.select('a[href*="/item/"]'):
        row = a.find_parent("div", class_="row")
        if row is None or id(row) in vistos:
            continue
        vistos.add(id(row))
        m = re.search(r"/item/(\d+)", a.get("href", ""))
        if not m:
            continue
        corpo = txt(row, "\n")
        cid = re.search(r"Cidade:\s*([^\n]+?)\s*/\s*([A-Z]{2})\b", corpo)
        desc_el = row.find("b", string=re.compile(r"Descri"))
        desc = limpa(txt(desc_el.parent).split(":", 1)[-1]) if desc_el is not None else ""
        status = txt(row.select_one(".label_lote"))
        h5v = next((h for h in row.find_all("h5") if re.search(r"lance|valor", txt(h), re.I)), None)
        valor_txt = txt(h5v.find_next("h4")) if h5v is not None else ""
        cat = next((txt(h) for h in row.find_all("h5") if h is not h5v), "")
        foto = ""
        for el in row.select('[style*="url("]'):
            mm = re.search(r"url\(['\"]?([^'\")]+)", el.get("style", ""))
            if mm:
                foto = mm[1]
                break
        itens.append({"id": m[1], "link": _dg_abs(a["href"]), "lote": txt(row.find("h4")), "categoria": cat,
                      "cidade": limpa(cid[1]) if cid else "", "uf": cid[2] if cid else "", "descricao": desc,
                      "status": status, "rotulo_valor": txt(h5v), "valor": num_br(valor_txt), "foto": foto})
    return info, itens


def lote_dg(item: dict, info: dict) -> Lote | None:
    if _RE_FIM.search(item.get("status") or ""):
        return None
    uf = item.get("uf") or uf_do_texto(info.get("titulo")) or uf_do_texto(item.get("descricao"))
    if not aceita_uf(uf):
        return None
    base = f"{item.get('categoria') or ''} {item.get('descricao')[:250]}"
    if _RE_DG_VEIC.search(item.get("categoria") or "") or (re.search(r"sucata", item.get("categoria") or "", re.I)
                                                          and re.search(r"placa|renavam|chassi", item.get("descricao") or "", re.I)):
        categoria = VEICULOS
    elif _RE_DG_IMOV.search(item.get("categoria") or "") or _RE_DG_IMOV.search(base):
        categoria = IMOVEIS
    else:
        return None
    tags = " ".join(info.get("tags") or []).upper()
    if info.get("venda_direta"):
        modalidade = M_VDIRETA
    elif "EXTRAJUDICIAL" in tags:
        modalidade = M_EXTRA
    elif "JUDICIAL" in tags:
        modalidade = M_JUDICIAL
    elif "ADMINISTRATIVO" in tags or "PÚBLICO" in tags or "PUBLICO" in tags:
        modalidade = M_GOV
    else:
        modalidade = M_JUDICIAL if RE_JUDICIAL.search(info.get("titulo") or "") else M_EXTRA
    desc = item.get("descricao") or ""
    if desc:
        titulo = desc if len(desc) <= 100 else desc[:100].rsplit(" ", 1)[0] + "…"
    else:
        titulo = titulo_pt(item.get("categoria") or "Lote") + (f" – {item['cidade']}/{uf}" if item.get("cidade") else "")
    l = Lote(fonte="Daniel Garcia Leilões", categoria=categoria, modalidade=modalidade, titulo=titulo,
             link=item["link"], id_fonte=item["id"], cidade=titulo_pt(item.get("cidade") or ""), uf=uf, descricao=desc,
             modalidade_original=" / ".join(v for v in ["Daniel Garcia – " + " | ".join(t for t in info.get("tags") or [] if t),
                                                        info.get("titulo") or ""] if v),
             leiloeiro=info.get("leiloeiro") or "", status=item.get("status") or "")
    l.tipo = tipo_imovel(base) if categoria == IMOVEIS else tipo_veiculo(base)
    if re.search(r"maior|atual", item.get("rotulo_valor") or "", re.I):
        l.lance_atual = item.get("valor")
    else:
        l.valor_minimo = item.get("valor")
    l.det("Lote", item.get("lote"))
    l.det("Leilão", info.get("titulo"))
    l.det("Categoria no site", item.get("categoria"))
    l.foto(item.get("foto") or "")
    if (m := RE_CNJ.search(desc)):
        l.processo = m[0]
    return l


def parse_dg_item(html: str, l: Lote):
    s = soup(html)
    for el in s(["script", "style"]):
        el.decompose()
    t = txt(s)

    def campo(rotulo, ate):
        m = re.search(rotulo + r"\s*(.*?)\s*(?:" + ate + r")", t)
        return limpa(m[1]) if m else ""

    if (com := campo(r"Comitente:", r"Cidade:|Endere[çc]o:|Descri[çc][ãa]o:")):
        l.comitente = com
    if (end := campo(r"Endere[çc]o:", r"Descri[çc][ãa]o:")):
        l.endereco = end
    pr = re.findall(r"Data (\d)\s*[º°ªo]?\s*Leil[ãa]o:\s*(\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2})?)\s*Lance Inicial:\s*R\$\s*([\d.,]+)", t)
    if pr:
        l.pracas = []
        for n, data, valor in pr:
            l.praca(f"{n}º leilão", data, num_br(valor))
    elif (m := re.search(r"Data do Leilão:\s*(\d{2}/\d{2}/\d{4}(?: \d{2}:\d{2})?)", t)):
        l.data_encerramento = fmt_data(parse_data(m[1]))
        if (mv := re.search(r"Data do Leilão:.{0,40}?Lance Inicial:\s*R\$\s*([\d.,]+)", t)):
            l.valor_minimo = num_br(mv[1])
    if (m := re.search(r"Valor de Avalia[çc][ãa]o:\s*R\$\s*([\d.,]+)", t)):
        l.valor_avaliacao = num_br(m[1])
    if (m := re.search(r"Comiss[ãa]o:\s*([\d,.]+\s*%)", t)):
        l.det("Comissão do leiloeiro", m[1].replace(" ", ""))
    if (m := re.search(r"Incremento M[íi]nimo:\s*(R\$\s*[\d.,]+)", t)):
        l.det("Incremento", m[1])
    if (m := re.search(r"visualiza[çc][õo]es:\s*(\d+)", t, re.I)):
        l.det("Visitas", m[1])
    st = txt(s.select_one("#status_lote"))
    if st:
        l.status = st
    b = s.find("b", string=re.compile(r"Descri[çc][ãa]o"))
    if b is not None:
        d = limpa(txt(b.parent).split(":", 1)[-1])
        if len(d) > len(l.descricao):
            l.descricao = d
    if not l.processo and (m := RE_CNJ.search(l.descricao + " " + t)):
        l.processo = m[0]
    for a in s.select("a[href]"):
        if re.search(r"\.pdf(\?|$)", a.get("href", ""), re.I):
            l.doc(txt(a) or "Documento", urljoin(DG + "/", a["href"]))
    for el in s.select('img[src*="/bens/"], a[href*="/watermark/"], [style*="/watermark/"]'):
        u = el.get("src") or el.get("href") or ""
        if not u and (mm := re.search(r"url\(['\"]?([^'\")]+)", el.get("style", ""))):
            u = mm[1]
        if re.search(r"\.(jpe?g|png|webp)(\?|$)", u, re.I):
            l.foto(u)


class DanielGarcia(Fonte):
    chave, nome, site, categorias = "danielgarcia", "Daniel Garcia Leilões", DG, (IMOVEIS, VEICULOS)

    def _leilao(self, lid: str, venda_direta: bool) -> list[Lote]:
        url = f"{DG}/leilao/{lid}/lotes"
        html = decodifica(self.http.get(url))
        info, itens = parse_dg_leilao(html, venda_direta)
        paginas = [int(p) for p in re.findall(r"/leilao/%s/lotes\?page=(\d+)" % lid, html)]
        for p in range(2, min(max(paginas or [1]), 30) + 1):
            itens += parse_dg_leilao(decodifica(self.http.get(url, params={"page": p})), venda_direta)[1]
        lotes, vistos = [], set()
        for it in itens:
            if it["id"] in vistos:
                continue
            vistos.add(it["id"])
            if (l := lote_dg(it, info)):
                lotes.append(l)
        return lotes

    def coletar(self):
        leiloes: dict[str, bool] = {}
        for url, vd in ((DG + "/leiloes", False), (DG + "/leiloes/venda-direta", True)):
            try:
                html = decodifica(self.http.get(url))
            except FonteErro:
                if vd:
                    continue
                raise
            for lid in re.findall(r"/leilao/(\d+)/lotes", html):
                leiloes[lid] = leiloes.get(lid, False) or vd
        lotes, falhas = [], 0
        with ThreadPoolExecutor(max_workers=max(1, self.cfg.workers)) as ex:
            for fut in as_completed([ex.submit(self._leilao, lid, vd) for lid, vd in leiloes.items()]):
                try:
                    lotes += fut.result()
                except Exception as e:  # noqa: BLE001
                    falhas += 1
                    log.debug("Daniel Garcia: leilão falhou: %s", e)
        if falhas:
            self.avisos.append(f"{falhas} de {len(leiloes)} leilões não puderam ser lidos")
        log.info("[%s] %d lotes (%d leilões verificados)", self.nome, len(lotes), len(leiloes))
        self.detalhar(lotes, lambda l: parse_dg_item(decodifica(self.http.get(l.link)), l))
        return lotes


# ======================================================================================
# 11) BOLSA DE LEILÕES (Plataforma Leiloar) — veículos e imóveis
# ======================================================================================
BOLSA = "https://www.bolsadeleiloes.com.br"
BOLSA_CATEGORIAS = ((VEICULOS, 4), (IMOVEIS, 1))
BOLSA_MG = "11"  # estado_id de Minas Gerais na plataforma


def _bolsa_ids_categoria(html: str, padrao: int) -> list[str]:
    form = soup(html).find("form", action=re.compile(r"pesquisaAvancada"))
    ids = [i.get("value") for i in (form.find_all("input", attrs={"name": "data[Bem][categoria_id][]"}) if form else [])]
    return [i for i in ids if i] or [str(padrao)]


def parse_bolsa_cards(html: str, categoria: str) -> list[Lote]:
    s = soup(html)
    lotes = []
    for c in s.select("div.bem-card"):
        a = c.select_one('a[href*="/lote/"]')
        if a is None:
            continue
        m = re.search(r"/lote/(\d+)", a.get("href", ""))
        est = c.find("input", attrs={"name": "data[Bem][estado_id]"})
        loc = txt(c.select_one(".bem-card-localizacao"))
        cidade, _, uf = loc.rpartition(",")
        uf = uf.strip().upper()
        if not nacional() and ((est is not None and est.get("value") != BOLSA_MG) or (est is None and uf != "MG")):
            continue
        if uf not in UFS:
            uf = "MG" if est is not None and est.get("value") == BOLSA_MG else ""
        badges = [txt(b) for b in c.select(".bem-card-status .badge")]
        if any(_RE_FIM.search(b) for b in badges):
            continue
        titulo = txt(c.select_one(".bem-card-descricao")) or f"Lote {m[1] if m else ''}"
        l = Lote(fonte="Bolsa de Leilões", categoria=categoria,
                 modalidade=M_JUDICIAL if RE_JUDICIAL.search(titulo) else M_EXTRA, titulo=titulo,
                 link=urljoin(BOLSA, a["href"]), id_fonte=m[1] if m else "", cidade=titulo_pt(cidade), uf=uf,
                 modalidade_original="Bolsa de Leilões", status=" / ".join(badges))
        l.tipo = tipo_imovel(titulo) if categoria == IMOVEIS else tipo_veiculo(titulo)
        lance = c.select_one(".bem-card-lance")
        if lance is not None:
            rot, h4 = txt(lance.find("span")), lance.find("h4")
            if h4 is not None and h4.find("span"):
                h4.find("span").decompose()
            v = num_br(txt(h4))
            if v and re.search(r"[úu]ltimo|atual", rot, re.I):
                l.lance_atual = v
            elif v:
                l.valor_minimo = v
        for sp in c.select(".bem-card-contadores span"):
            if sp.get("title") in ("Visitas", "Lances"):
                l.det(sp["title"], txt(sp))
        l.det("Encerramento", txt(c.select_one(".bem-encerramento")))
        img = c.select_one("img.bem-card-foto")
        if img is not None:
            l.foto(img.get("src", ""))
        lotes.append(l)
    return lotes


def parse_bolsa_detalhe(html: str, l: Lote):
    s = soup(html)
    for div in s.select(".bem-lance-valores > div"):
        rot = txt(div.find("strong")).rstrip(":").strip()
        val = limpa(txt(div)[len(txt(div.find("strong"))):])
        k = chave(rot)
        if k == "ultimolance" and num_br(val):
            l.lance_atual = num_br(val)
        elif k == "lanceinicial" and num_br(val):
            l.valor_minimo = num_br(val)
        elif k == "incremento":
            l.det("Incremento", val)
        elif k in ("lances", "visitas"):
            l.det(rot, val)
    tag = s.select_one(".bem-descricao a i.fa-tag")
    if tag is not None:
        l.det("Categoria", txt(tag.parent))
    h = s.select_one(".bem-descricao h5")
    if h is not None and txt(h):
        l.titulo = txt(h)
    lei = s.select_one(".bem-leilao")
    if lei is not None:
        nome = txt(lei.select_one("h6"))
        l.det("Leilão", nome)
        if RE_JUDICIAL.search(nome):
            l.modalidade = M_JUDICIAL
        for b in lei.find_all("b"):
            rot = txt(b).rstrip(":").upper()
            val = txt(b.find_next_sibling()) if rot == "DATA" else limpa(txt(b.parent)[len(txt(b)):])
            if rot == "DATA" and parse_data(val):
                l.data_encerramento = fmt_data(parse_data(val))
            elif rot == "LOCAL":
                l.det("Local do leilão", val)
    for pid, nome in (("tab-visitacao", "Visitação"), ("tab-retirada", "Retirada"), ("tab-obs", "Observações")):
        p = s.find(id=pid)
        if p is not None and txt(p):
            l.det(nome, txt(p))
    if not l.descricao and l.detalhes.get("Observações"):
        l.descricao = l.detalhes["Observações"]
    for a in s.select('a[href*="bem_foto"]'):
        l.foto(a.get("href", ""))


class Bolsa(Fonte):
    chave, nome, site, categorias = "bolsa", "Bolsa de Leilões", BOLSA, (IMOVEIS, VEICULOS)

    def coletar(self):
        lotes, vistos = [], set()
        for categoria, cat in BOLSA_CATEGORIAS:
            sessao = self.http.nova_sessao()  # a busca guarda o filtro na sessão (cookies)
            ids = _bolsa_ids_categoria(decodifica(self.http.get(f"{BOLSA}/por-categoria/{cat}", sessao=sessao)), cat)
            corpo = [("_method", "POST"), ("limpa_filtro", "1")] + [("data[Bem][categoria_id][]", i) for i in ids] + \
                    ([] if nacional() else [("data[Bem][estado_id]", BOLSA_MG)])
            html = decodifica(self.http.post(BOLSA + "/bens/pesquisaAvancada", sessao=sessao, data=corpo,
                                             headers={"Referer": f"{BOLSA}/por-categoria/{cat}"}))
            pagina = 1
            while True:
                novos = [l for l in parse_bolsa_cards(html, categoria) if l.id_fonte not in vistos]
                for l in novos:
                    vistos.add(l.id_fonte)
                lotes += novos
                pagina += 1
                if pagina > (200 if nacional() else 30) or f"/bens/pesquisaAvancada/page:{pagina}" not in html:
                    break
                html = decodifica(self.http.get(f"{BOLSA}/bens/pesquisaAvancada/page:{pagina}", sessao=sessao))
        log.info("[%s] %d lotes", self.nome, len(lotes))
        self.detalhar(lotes, lambda l: parse_bolsa_detalhe(decodifica(self.http.get(l.link)), l))
        return lotes


# ======================================================================================
# 12) SODRÉ SANTORO — veículos (financeiras, seguradoras, frotas) e imóveis
# ======================================================================================
SODRE = "https://www.sodresantoro.com.br"
SODRE_FECHADOS = {5, 6, 7, 9}  # 5 = vendido, 9 = retirado
SODRE_TIPOS = {"carros": "Carro / utilitário", "utilitarios leves": "Carro / utilitário", "peruas": "Carro / utilitário",
               "van leve": "Carro / utilitário", "motos": "Moto", "caminhoes": "Caminhão / reboque",
               "implementos rod.": "Caminhão / reboque", "utilit. pesados": "Caminhão / reboque", "tratores": "Trator",
               "onibus": "Ônibus"}
_SODRE_QUERY = {"bool": {"filter": [
    {"bool": {"should": [{"bool": {"must": [{"term": {"auction_status": "online"}}]}},
                         {"bool": {"must": [{"term": {"auction_status": "aberto"}}],
                                   "must_not": [{"terms": {"lot_status_id": [5, 7]}}]}}],
              "minimum_should_match": 1}},
    {"bool": {"must_not": [{"term": {"lot_test": True}}]}},
]}}


def _cap(s: str) -> str:
    """'guarulhos ii' -> 'Guarulhos II' ; 'hdi seguros' -> 'Hdi Seguros'."""
    out = []
    for i, w in enumerate(limpa(s).lower().split(" ")):
        if re.fullmatch(r"[iv]{1,4}", w):
            out.append(w.upper())
        elif i and w in _MINUSC:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out) if s else ""


def parse_sodre(x: dict, todos: bool = False) -> Lote | None:
    if int(x.get("lot_status_id") or 0) in SODRE_FECHADOS or x.get("lot_test"):
        return None
    seg = (x.get("segment_slug") or "").lower()
    categoria = VEICULOS if seg == "veiculos" else IMOVEIS if seg == "imoveis" else None
    if not categoria:
        return None
    loc = limpa(x.get("lot_location"))
    cidade, _, uf = loc.rpartition("|") if "|" in loc else loc.rpartition("/")
    uf = uf.strip().upper()
    endereco = limpa(x.get("lot_location_address"))
    if uf not in UFS:
        uf = uf_do_texto(endereco.replace(" - ", "/").upper()) or ""
        cidade = cidade or ""
    if not todos and not aceita_uf(uf):
        return None
    titulo = limpa(x.get("lot_title")).upper() or f"LOTE {x.get('lot_number')}"
    fab, mod = x.get("lot_year_manufacture"), x.get("lot_year_model")
    if categoria == VEICULOS and fab and mod:
        titulo += f" {fab}/{mod}"
    cli = limpa(x.get("client_name"))
    if x.get("lot_is_judicial"):
        modalidade = M_JUDICIAL
    elif re.search(r"secretaria|prefeitura|governo|munic[íi]pio|tribunal|departamento|pol[íi]cia|autarquia", cli, re.I):
        modalidade = M_GOV
    else:
        modalidade = M_EXTRA
    l = Lote(fonte="Sodré Santoro", categoria=categoria, modalidade=modalidade, titulo=titulo,
             link=f"https://leilao.sodresantoro.com.br/leilao/{x.get('auction_id')}/lote/{x.get('lot_id')}/",
             id_fonte=str(x.get("lot_id") or x.get("id") or ""), cidade=_cap(cidade), uf=uf or "",
             endereco=endereco, modalidade_original=" / ".join(v for v in ("Sodré Santoro", limpa(x.get("auction_name"))) if v),
             comitente=_cap(cli), leiloeiro=_cap(x.get("auctioneer_name") or ""), status=limpa(x.get("lot_status")),
             valor_minimo=num_br(x.get("bid_initial")) or None,
             lance_atual=(num_br(x.get("bid_actual")) or None) if x.get("bid_has_bid") else None,
             data_encerramento=fmt_data(parse_data(x.get("lot_date_end") or x.get("auction_date_end"))))
    cat = sem_acento(limpa(x.get("lot_category"))).lower()
    l.tipo = SODRE_TIPOS.get(cat) or (tipo_veiculo(titulo) if categoria == VEICULOS else tipo_imovel(titulo))
    partes = [limpa(conserta_acentos(x.get(k) or "")) for k in ("lot_description", "lot_message", "lot_note")]
    l.descricao = "\n\n".join(p for p in partes if p)
    l.det("Leilão", f"{x.get('auction_id')} – {limpa(x.get('auction_name'))}")
    l.det("Lote nº", x.get("lot_number"))
    l.det("Vistoria nº", x.get("lot_inspection_number"))
    sin = limpa(x.get("lot_sinister"))
    l.det("Sinistro", sin)
    if sin and "sem sinistro" not in sin.lower():
        l.selo(f"Sinistro: {sin}")
    ori = limpa(x.get("lot_origin"))
    l.det("Origem", ori)
    if ori.lower() == "seguro":
        l.selo("Veículo de seguradora")
    if x.get("lot_is_scrap"):
        l.selo("Sucata")
    for k, nome in (("lot_brand", "Marca"), ("lot_model", "Modelo"), ("lot_plate", "Placa"), ("lot_color", "Cor"),
                    ("lot_fuel", "Combustível"), ("lot_transmission", "Câmbio"), ("lot_air", "Ar-condicionado"),
                    ("lot_direction", "Direção hidráulica"), ("lot_financeable", "Financiamento")):
        v = limpa(x.get(k))
        l.det(nome, {"Nao": "Não", "Sim": "Sim"}.get(v, v.upper() if k in ("lot_brand", "lot_model") else v))
    if fab or mod:
        l.det("Ano fab./modelo", f"{fab or '?'}/{mod or '?'}")
    if x.get("lot_km"):
        l.det("Quilometragem", fmt_num(float(x["lot_km"]), 0) + " km")
    if x.get("lot_optionals"):
        l.det("Opcionais", ", ".join(o.replace("-", " ") for o in x["lot_optionals"]))
    l.det("Taxas", conserta_acentos(x.get("lot_rate_information") or ""))
    l.det("Visitas", x.get("lot_visits"))
    l.det("Pátio", _cap(cidade) + (f"/{uf}" if uf else ""))
    for u in x.get("lot_pictures") or []:
        l.foto(str(u).split("?")[0])
    return l


class Sodre(Fonte):
    chave, nome, site, categorias = "sodre", "Sodré Santoro", SODRE, (IMOVEIS, VEICULOS)

    def coletar(self):
        cab = {"Accept": "application/json", "Content-Type": "application/json", "Origin": SODRE, "Referer": SODRE + "/veiculos/lotes"}
        lotes, vistos, ufs, ini, total = [], set(), {}, 0, 1
        while ini < total and ini < 20000:
            corpo = {"indices": ["veiculos", "imoveis"], "query": _SODRE_QUERY, "from": ini, "size": 500}
            j = self.http.post(SODRE + "/api/search-lots", headers=cab, data=json.dumps(corpo)).json()
            res = j.get("results") or []
            total = int(j.get("total") or 0)
            for x in res:
                if x.get("lot_id") in vistos:
                    continue
                vistos.add(x.get("lot_id"))
                uf = (limpa(x.get("lot_location")).rpartition("|")[2] or "?").upper()
                ufs[uf] = ufs.get(uf, 0) + 1
                try:
                    l = parse_sodre(x, self.cfg.sodre_todos or nacional())
                except Exception as e:  # noqa: BLE001
                    log.debug("Sodré: lote %s ignorado: %s", x.get("lot_id"), e)
                    continue
                if l:
                    lotes.append(l)
            if not res:
                break
            ini += len(res)
        if not lotes and not self.cfg.sodre_todos and not nacional() and vistos:
            fora = ", ".join(f"{k} ({v})" for k, v in sorted(ufs.items(), key=lambda kv: -kv[1]))
            self.avisos.append(f"nenhum pátio em MG hoje — lotes por UF: {fora}. "
                               "Use --sodre-todos-patios para incluir os outros pátios")
        log.info("[%s] %d lotes%s", self.nome, len(lotes), "" if self.cfg.sodre_todos or nacional() else " em MG")
        return lotes


FONTES = [Caixa, Zuk, Mega, Superbid, Franco, LeiloesJudiciaisMG, GPLeiloes, DanielGarcia, Bolsa, Detran, Palacio, Sodre]


# --------------------------------------------------------------------------------------
# Pós-processamento
# --------------------------------------------------------------------------------------
def finalizar(lotes: list[Lote]) -> list[Lote]:
    agora = datetime.now()
    # nomes de cidade: unifica grafias (prefere a versão com acento)
    melhor: dict[str, str] = {}
    for l in lotes:
        if not l.cidade:
            continue
        k = chave(l.cidade)
        atual = melhor.get(k)
        if atual is None or (sem_acento(atual) == atual and sem_acento(l.cidade) != l.cidade):
            melhor[k] = l.cidade
    for l in lotes:
        if l.cidade:
            l.cidade = melhor.get(chave(l.cidade), l.cidade)
        l.bairro = titulo_pt(l.bairro)
        valores = [p["valor"] for p in l.pracas if p.get("valor")]
        if valores:
            menor = min(valores)
            l.valor_minimo = min(l.valor_minimo, menor) if l.valor_minimo else menor
        futuras = sorted((p for p in l.pracas if p.get("iso") and p["iso"] >= agora.isoformat()), key=lambda p: p["iso"])
        prox = futuras[0] if futuras else None
        l.preco_ref = (l.lance_atual if l.lance_atual else None) or (prox["valor"] if prox and prox.get("valor") else None) \
            or l.valor_minimo or l.valor_avaliacao
        base = l.valor_minimo or l.preco_ref
        if l.desconto is None and l.valor_avaliacao and base and base < l.valor_avaliacao:
            l.desconto = round((1 - base / l.valor_avaliacao) * 100, 1)
        enc = parse_data(l.data_encerramento)
        if prox:
            l.data_ref = prox["iso"]
        elif enc and enc >= agora:
            l.data_ref = enc.isoformat()
        if not l.data_encerramento and l.pracas and l.pracas[-1].get("data"):
            l.data_encerramento = l.pracas[-1]["data"]
        if len(l.descricao) > 8000:
            l.descricao = l.descricao[:8000] + "…"
    return lotes


def lote_dict(l: Lote) -> dict:
    d = asdict(l)
    return d


# --------------------------------------------------------------------------------------
# Relatório HTML
# --------------------------------------------------------------------------------------
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Leilões em Minas Gerais – __GERADO__</title>
<style>
[hidden] { display: none !important; }
:root{--bg:#f5f6f8;--card:#fff;--tx:#1b2230;--mut:#5d6878;--ln:#e2e6eb;--ac:#1d5bbf;--ac2:#e7effb;--ok:#127a3a;--warn:#9a5600;--warnbg:#fff2df;--bad:#b42318;--badbg:#fde9e7;--chip:#eef1f5;--sh:0 1px 2px rgba(16,24,40,.05),0 1px 3px rgba(16,24,40,.07)}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#171c24;--tx:#e5e9ef;--mut:#9aa5b5;--ln:#2a313c;--ac:#78a9ff;--ac2:#1a2945;--ok:#5fd08a;--warn:#ffb85c;--warnbg:#3a2a12;--bad:#ff8a80;--badbg:#3b1a18;--chip:#222a36;--sh:none}}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Ubuntu,Arial,sans-serif;background:var(--bg);color:var(--tx)}
.wrap{max-width:1440px;margin:0 auto;padding:0 20px}
header.top{padding:26px 0 10px}
h1{font-size:26px;margin:0 0 4px;letter-spacing:-.01em}
.sub{color:var(--mut)}
.fontes{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
.fst{font-size:12px;padding:3px 9px;border-radius:999px;background:var(--chip);color:var(--mut)}
.fst b{color:var(--tx);font-weight:600}
.fst.err{background:var(--badbg);color:var(--bad)}
.fst.av{background:var(--warnbg);color:var(--warn)}
.tabs{display:flex;gap:2px;border-bottom:1px solid var(--ln);margin-top:10px}
.tab{border:0;background:none;padding:10px 18px;font:inherit;font-size:15px;font-weight:600;color:var(--mut);border-bottom:3px solid transparent;cursor:pointer}
.tab.on{color:var(--tx);border-color:var(--ac)}
.tab span{font-weight:500;color:var(--mut);margin-left:4px}
.barra{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--ln);box-shadow:0 2px 6px rgba(0,0,0,.03)}
.filtros{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 0}
.filtros input,.filtros select{font:inherit;padding:7px 10px;border:1px solid var(--ln);border-radius:8px;background:var(--card);color:var(--tx);min-width:0}
#q{flex:1 1 280px}
.filtros .num{width:120px}
.filtros label{display:flex;gap:6px;align-items:center;color:var(--mut);font-size:13px}
.navmod{display:flex;flex-wrap:wrap;gap:6px;padding:0 0 10px}
.chip{border:1px solid var(--ln);background:var(--card);border-radius:999px;padding:4px 11px;font:inherit;font-size:12.5px;cursor:pointer;color:var(--tx)}
.chip b{font-weight:600;margin-left:5px;color:var(--mut)}
.chip:hover{border-color:var(--ac)}
#resumo{color:var(--mut);font-size:13px;margin-left:auto}
main{padding:6px 0 60px}
section.mod{margin:26px 0 6px;scroll-margin-top:120px}
section.mod>h2{font-size:19px;margin:0;display:flex;gap:10px;align-items:center}
.cnt{font-size:12px;font-weight:600;padding:2px 9px;border-radius:999px;background:var(--ac2);color:var(--ac)}
.mdesc{color:var(--mut);margin:4px 0 14px;max-width:920px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--ln);border-radius:12px;overflow:hidden;box-shadow:var(--sh);display:flex;flex-direction:column;min-width:0}
.foto{aspect-ratio:16/10;background:var(--chip);position:relative;display:flex;align-items:center;justify-content:center;color:var(--mut);font-size:12px}
.foto img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}
.foto .nf{position:absolute;bottom:6px;right:6px;background:rgba(0,0,0,.62);color:#fff;font-size:11px;padding:2px 7px;border-radius:6px}
.cb{padding:12px 14px 10px;display:flex;flex-direction:column;gap:6px;flex:1}
.selos{display:flex;flex-wrap:wrap;gap:4px}
.selo{font-size:11px;padding:2px 8px;border-radius:999px;background:var(--chip);color:var(--mut)}
.selo.f{background:var(--ac2);color:var(--ac);font-weight:600}
.selo.al{background:var(--warnbg);color:var(--warn);font-weight:600}
.tit{font-weight:600;font-size:15px;line-height:1.3;overflow-wrap:anywhere}
.loc{color:var(--mut);font-size:13px}
.preco{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin-top:2px}
.preco .v{font-size:21px;font-weight:700;letter-spacing:-.01em}
.preco .rot{font-size:11.5px;color:var(--mut)}
.preco .d{color:var(--ok);font-weight:700;font-size:13px}
.lin{color:var(--mut);font-size:12.5px}
.pracas{font-size:12.5px;border-top:1px dashed var(--ln);padding-top:6px;display:grid;gap:3px}
.pracas .p{display:flex;justify-content:space-between;gap:8px}
.pracas .p.passou{color:var(--mut);text-decoration:line-through;opacity:.8}
.pracas .p.prox{font-weight:600}
.acoes{display:flex;gap:8px;margin-top:auto;padding-top:8px}
.btn{font:inherit;font-size:13px;padding:6px 11px;border-radius:8px;border:1px solid var(--ln);background:var(--card);color:var(--tx);cursor:pointer;text-decoration:none;display:inline-block}
.btn.pri{background:var(--ac);border-color:var(--ac);color:#fff}
details.mais{border-top:1px solid var(--ln)}
details.mais>summary{padding:9px 14px;cursor:pointer;color:var(--ac);font-weight:600;font-size:13px;list-style:none}
details.mais>summary::-webkit-details-marker{display:none}
details.mais>summary::before{content:"▸ "}
details.mais[open]>summary::before{content:"▾ "}
.det{padding:0 14px 14px;font-size:13px}
.det h4{margin:12px 0 6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--mut)}
.det table{width:100%;border-collapse:collapse}
.det td{border-top:1px solid var(--ln);padding:5px 6px;vertical-align:top;overflow-wrap:anywhere}
.det td:first-child{color:var(--mut);width:38%}
.det .desc{white-space:pre-wrap;background:var(--bg);padding:9px;border-radius:8px;max-height:300px;overflow:auto;font-size:12.5px}
.det ul{margin:0;padding-left:18px}
.thumbs{display:flex;gap:4px;flex-wrap:wrap}
.thumbs img{width:76px;height:57px;object-fit:cover;border-radius:5px;display:block}
.mostrar{margin:14px auto 0;display:block}
.vazio{color:var(--mut);padding:40px;text-align:center}
footer{color:var(--mut);font-size:12.5px;padding:10px 0 40px;border-top:1px solid var(--ln)}
@media (max-width:640px){.wrap{padding:0 12px}.grid{grid-template-columns:1fr}.barra{position:static}
  .filtros select,.filtros .num{flex:1 1 calc(50% - 4px);width:auto}#resumo{margin-left:0}h1{font-size:22px}}
</style>
</head>
<body>
<div class="wrap">
<header class="top">
  <h1>Leilões em Minas Gerais</h1>
  <div class="sub" id="sub"></div>
  <div class="fontes" id="fontes"></div>
  <div class="tabs" id="tabs"></div>
</header>
</div>
<div class="barra"><div class="wrap">
  <div class="filtros">
    <input id="q" type="search" placeholder="Buscar (ex.: apartamento savassi, hilux 2020, matrícula, processo…)">
    <select id="cidade"><option value="">Todas as cidades</option></select>
    <select id="tipo"><option value="">Todos os tipos</option></select>
    <select id="fonte"><option value="">Todas as fontes</option></select>
    <input id="min" class="num" type="number" min="0" step="1000" placeholder="Preço mín.">
    <input id="max" class="num" type="number" min="0" step="1000" placeholder="Preço máx.">
    <select id="ordem">
      <option value="data">Data mais próxima</option>
      <option value="menor">Menor preço</option>
      <option value="maior">Maior preço</option>
      <option value="desconto">Maior desconto</option>
    </select>
    <label><input id="fotos" type="checkbox"> só com foto</label>
    <button class="btn" id="csv" title="Baixa os lotes filtrados em CSV (abre no Excel)">Exportar CSV</button>
    <span id="resumo"></span>
  </div>
  <div class="navmod" id="navmod"></div>
</div></div>
<div class="wrap"><main id="lista"></main>
<footer>
  Gerado por <code>leiloes_mg.py</code> em <span id="ger"></span>. Os dados foram copiados dos sites das fontes no momento da coleta e podem mudar a qualquer hora.
  Antes de dar um lance, leia o edital e confira a página oficial do lote (débitos, ocupação, comissão do leiloeiro, condições de pagamento e retirada).
</footer></div>
<script id="dados" type="application/json">__DADOS__</script>
<script>
(() => {
"use strict";
const D = JSON.parse(document.getElementById("dados").textContent);
const L = D.lotes, INFO = D.modalidades, ORDEM = D.ordem_modalidades;
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const okUrl = u => /^https?:\/\//i.test(u || "");
const brl = v => v == null ? "" : Number(v).toLocaleString("pt-BR", {style: "currency", currency: "BRL"});
const pct = v => Number(v).toLocaleString("pt-BR", {maximumFractionDigits: 1}) + "%";
const norm = s => String(s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase();
const slug = s => norm(s).replace(/[^a-z0-9]+/g, "-");
const AGORA = (d => new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 19))(new Date());
L.forEach((l, i) => {
  l._i = i;
  l._t = l.data_ref ? Date.parse(l.data_ref) : Infinity;
  l._b = norm([l.titulo, l.descricao, l.cidade, l.bairro, l.endereco, l.tipo, l.comitente, l.leiloeiro, l.processo,
               l.fonte, l.modalidade, l.modalidade_original, (l.selos || []).join(" "),
               Object.entries(l.detalhes || {}).map(e => e.join(" ")).join(" ")].join(" "));
});
const cats = D.categorias.filter(c => L.some(l => l.categoria === c));
const st = {cat: cats[0] || D.categorias[0], q: "", cidade: "", tipo: "", fonte: "", min: "", max: "", ordem: "data", fotos: false};
const PAG = 30;

$("#sub").textContent = `${L.length.toLocaleString("pt-BR")} lotes coletados em ${D.gerado_em}` + (D.modo ? ` · ${D.modo}` : "");
$("#ger").textContent = D.gerado_em;
$("#fontes").innerHTML = D.status.map(s => {
  if (!s.ok) return `<span class="fst err" title="${esc(s.erro)}">✕ ${esc(s.fonte)}: falhou</span>`;
  const av = (s.avisos || []).length;
  return `<span class="fst${av ? " av" : ""}" title="${esc((s.avisos || []).join(" · "))}">${esc(s.fonte)} <b>${s.n}</b>${av ? " ⚠" : ""}</span>`;
}).join("");

function tabs() {
  $("#tabs").innerHTML = D.categorias.map(c => {
    const n = L.filter(l => l.categoria === c).length;
    return `<button class="tab${c === st.cat ? " on" : ""}" data-cat="${esc(c)}">${esc(c)}<span>${n.toLocaleString("pt-BR")}</span></button>`;
  }).join("");
}
function opcoes(sel, campo, rotulo) {
  const cont = {};
  L.forEach(l => { if (l.categoria === st.cat && l[campo]) cont[l[campo]] = (cont[l[campo]] || 0) + 1; });
  const ks = Object.keys(cont).sort((a, b) => a.localeCompare(b, "pt-BR"));
  const atual = st[sel];
  $("#" + sel).innerHTML = `<option value="">${rotulo}</option>` + ks.map(k => `<option value="${esc(k)}"${k === atual ? " selected" : ""}>${esc(k)} (${cont[k]})</option>`).join("");
  if (atual && !cont[atual]) st[sel] = "";
}
function filtrados() {
  const ws = norm(st.q).split(/\s+/).filter(Boolean);
  const mn = st.min === "" ? null : +st.min, mx = st.max === "" ? null : +st.max;
  return L.filter(l => l.categoria === st.cat
    && (!st.cidade || l.cidade === st.cidade) && (!st.tipo || l.tipo === st.tipo) && (!st.fonte || l.fonte === st.fonte)
    && (!st.fotos || (l.fotos || []).length)
    && (mn == null || (l.preco_ref != null && l.preco_ref >= mn)) && (mx == null || (l.preco_ref != null && l.preco_ref <= mx))
    && ws.every(w => l._b.includes(w)));
}
const ORD = {
  data: (a, b) => a._t - b._t || (a.preco_ref ?? 1e15) - (b.preco_ref ?? 1e15),
  menor: (a, b) => (a.preco_ref ?? 1e15) - (b.preco_ref ?? 1e15),
  maior: (a, b) => (b.preco_ref ?? -1) - (a.preco_ref ?? -1),
  desconto: (a, b) => (b.desconto ?? -1) - (a.desconto ?? -1),
};
const pos = m => { const i = ORDEM.indexOf(m); return i < 0 ? 99 : i; };

function card(l) {
  const foto = (l.fotos || []).find(okUrl);
  let proxMarcada = false;
  const pr = (l.pracas || []).map(p => {
    const passou = p.iso && p.iso < AGORA;
    let cls = passou ? "passou" : "";
    if (!passou && !proxMarcada && p.iso) { cls = "prox"; proxMarcada = true; }
    return `<div class="p ${cls}"><span>${esc(p.nome)}${p.data ? " · " + esc(p.data) : ""}</span><b>${p.valor != null ? brl(p.valor) : ""}</b></div>`;
  }).join("");
  const rot = l.lance_atual ? "lance atual" : (l.pracas || []).length ? "lance mínimo" : l.valor_minimo ? "lance inicial" : "valor";
  const ate = l.valor_minimo && l.preco_ref && l.valor_minimo < l.preco_ref - 0.5 ? "até " : "";
  return `<article class="card">
  <div class="foto">sem foto${foto ? `<img loading="lazy" referrerpolicy="no-referrer" src="${esc(foto)}" alt="" onerror="this.remove()">` : ""}${(l.fotos || []).length > 1 ? `<span class="nf">${l.fotos.length} fotos</span>` : ""}</div>
  <div class="cb">
    <div class="selos"><span class="selo f">${esc(l.fonte)}</span>${l.tipo ? `<span class="selo">${esc(l.tipo)}</span>` : ""}${(l.selos || []).map(s => `<span class="selo al">${esc(s)}</span>`).join("")}</div>
    <div class="tit">${esc(l.titulo)}</div>
    <div class="loc">${esc([l.bairro, l.cidade ? l.cidade + "/" + (l.uf || "MG") : ""].filter(Boolean).join(" · "))}</div>
    <div class="preco">${l.preco_ref != null ? `<span class="v">${brl(l.preco_ref)}</span><span class="rot">${rot}</span>` : `<span class="v">—</span>`}${l.desconto ? `<span class="d">${ate}−${pct(l.desconto)}</span>` : ""}</div>
    ${l.valor_avaliacao ? `<div class="lin">Avaliação: ${brl(l.valor_avaliacao)}</div>` : ""}
    ${pr ? `<div class="pracas">${pr}</div>` : (l.data_encerramento ? `<div class="lin">Encerramento: ${esc(l.data_encerramento)}</div>` : "")}
    <div class="lin">${esc(l.modalidade_original)}</div>
    <div class="acoes">${okUrl(l.link) ? `<a class="btn pri" href="${esc(l.link)}" target="_blank" rel="noopener">Abrir no site ↗</a>` : ""}</div>
  </div>
  <details class="mais" data-i="${l._i}"><summary>Todos os detalhes</summary><div class="det"></div></details>
</article>`;
}

function detalhe(l) {
  const rows = [];
  const add = (k, v) => { if (v != null && v !== "") rows.push(`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`); };
  add("Modalidade", l.modalidade); add("Modalidade na fonte", l.modalidade_original); add("Tipo", l.tipo);
  add("Endereço", l.endereco); add("Bairro", l.bairro); add("Cidade", l.cidade ? l.cidade + "/" + (l.uf || "MG") : "");
  add("Preço de referência", l.preco_ref != null ? brl(l.preco_ref) : ""); add("Lance atual", l.lance_atual ? brl(l.lance_atual) : "");
  add("Menor lance previsto", l.valor_minimo ? brl(l.valor_minimo) : ""); add("Valor de avaliação", l.valor_avaliacao ? brl(l.valor_avaliacao) : "");
  add("Desconto", l.desconto ? pct(l.desconto) : "");
  (l.pracas || []).forEach(p => add(p.nome, [p.data, p.valor != null ? brl(p.valor) : ""].filter(Boolean).join(" — ")));
  add("Encerramento", l.data_encerramento); add("Situação", l.status); add("Leiloeiro", l.leiloeiro);
  add("Comitente / vendedor", l.comitente); add("Processo", l.processo);
  Object.entries(l.detalhes || {}).forEach(([k, v]) => add(k, v));
  add("Código na fonte", l.id_fonte);
  const docs = (l.documentos || []).filter(d => okUrl(d.url)).map(d => `<li><a href="${esc(d.url)}" target="_blank" rel="noopener">${esc(d.nome)}</a></li>`).join("");
  const th = (l.fotos || []).filter(okUrl).map(u => `<a href="${esc(u)}" target="_blank" rel="noopener"><img loading="lazy" referrerpolicy="no-referrer" src="${esc(u)}" alt="" onerror="this.parentNode.remove()"></a>`).join("");
  return (l.descricao ? `<h4>Descrição</h4><div class="desc">${esc(l.descricao)}</div>` : "")
    + `<h4>Dados do lote</h4><table>${rows.join("")}</table>`
    + (docs ? `<h4>Documentos e links</h4><ul>${docs}</ul>` : "")
    + (th ? `<h4>Fotos (${l.fotos.length})</h4><div class="thumbs">${th}</div>` : "");
}

function render() {
  const arr = filtrados().sort(ORD[st.ordem]);
  const g = new Map();
  arr.forEach(l => { if (!g.has(l.modalidade)) g.set(l.modalidade, []); g.get(l.modalidade).push(l); });
  const nomes = [...g.keys()].sort((a, b) => pos(a) - pos(b) || g.get(b).length - g.get(a).length);
  $("#navmod").innerHTML = nomes.map(n => `<button class="chip" data-ir="m-${esc(slug(n))}">${esc(n)}<b>${g.get(n).length.toLocaleString("pt-BR")}</b></button>`).join("");
  $("#resumo").textContent = `${arr.length.toLocaleString("pt-BR")} lote${arr.length === 1 ? "" : "s"}`;
  const main = $("#lista");
  main.innerHTML = "";
  if (!arr.length) { main.innerHTML = `<div class="vazio">Nenhum lote com esses filtros.</div>`; return; }
  for (const n of nomes) {
    const lst = g.get(n);
    const sec = document.createElement("section");
    sec.className = "mod"; sec.id = "m-" + slug(n);
    const desc = (INFO[st.cat] || {})[n] || "";
    sec.innerHTML = `<h2>${esc(n)} <span class="cnt">${lst.length.toLocaleString("pt-BR")}</span></h2>${desc ? `<p class="mdesc">${esc(desc)}</p>` : ""}<div class="grid"></div>`;
    const grid = sec.querySelector(".grid");
    const btn = document.createElement("button");
    btn.className = "btn mostrar";
    let vistos = 0;
    const mais = () => {
      const fatia = lst.slice(vistos, vistos + PAG);
      grid.insertAdjacentHTML("beforeend", fatia.map(card).join(""));
      vistos += fatia.length;
      btn.hidden = vistos >= lst.length;
      btn.textContent = `Mostrar mais (${(lst.length - vistos).toLocaleString("pt-BR")} restantes)`;
    };
    btn.addEventListener("click", mais);
    sec.appendChild(btn);
    mais();
    main.appendChild(sec);
  }
}
function tudo() { tabs(); opcoes("cidade", "cidade", "Todas as cidades"); opcoes("tipo", "tipo", "Todos os tipos"); opcoes("fonte", "fonte", "Todas as fontes"); render(); }

document.addEventListener("toggle", e => {
  const d = e.target;
  if (d.matches && d.matches("details.mais") && d.open && !d.dataset.ok) {
    d.querySelector(".det").innerHTML = detalhe(L[+d.dataset.i]);
    d.dataset.ok = "1";
  }
}, true);
$("#tabs").addEventListener("click", e => { const b = e.target.closest(".tab"); if (b) { st.cat = b.dataset.cat; tudo(); } });
$("#navmod").addEventListener("click", e => { const b = e.target.closest(".chip"); if (b) document.getElementById(b.dataset.ir)?.scrollIntoView({behavior: "smooth"}); });
let tm;
const ligar = (id, campo, ev = "input") => $("#" + id).addEventListener(ev, e => {
  st[campo] = e.target.type === "checkbox" ? e.target.checked : e.target.value;
  clearTimeout(tm); tm = setTimeout(render, 180);
});
ligar("q", "q"); ligar("min", "min"); ligar("max", "max");
ligar("cidade", "cidade", "change"); ligar("tipo", "tipo", "change"); ligar("fonte", "fonte", "change");
ligar("ordem", "ordem", "change"); ligar("fotos", "fotos", "change");
$("#csv").addEventListener("click", () => {
  const arr = filtrados().sort(ORD[st.ordem]);
  const cols = [["Categoria", l => l.categoria], ["Modalidade", l => l.modalidade], ["Modalidade na fonte", l => l.modalidade_original],
    ["Fonte", l => l.fonte], ["Título", l => l.titulo], ["Tipo", l => l.tipo], ["Cidade", l => l.cidade], ["Bairro", l => l.bairro],
    ["Endereço", l => l.endereco], ["Preço de referência", l => l.preco_ref], ["Lance atual", l => l.lance_atual],
    ["Menor lance previsto", l => l.valor_minimo], ["Avaliação", l => l.valor_avaliacao], ["Desconto %", l => l.desconto],
    ["Praças", l => (l.pracas || []).map(p => `${p.nome} ${p.data} ${p.valor != null ? brl(p.valor) : ""}`.trim()).join(" | ")],
    ["Encerramento", l => l.data_encerramento], ["Leiloeiro", l => l.leiloeiro], ["Comitente", l => l.comitente],
    ["Processo", l => l.processo], ["Link", l => l.link],
    ["Detalhes", l => Object.entries(l.detalhes || {}).map(([k, v]) => `${k}: ${v}`).join(" | ")]];
  const cel = v => typeof v === "number" ? String(v).replace(".", ",") : '"' + String(v ?? "").replace(/"/g, '""') + '"';
  const csv = [cols.map(c => cel(c[0]))].concat(arr.map(l => cols.map(c => cel(c[1](l))))).map(r => r.join(";")).join("\r\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob(["﻿" + csv], {type: "text/csv;charset=utf-8"}));
  a.download = `leiloes_mg_${slug(st.cat)}.csv`;
  document.body.appendChild(a); a.click(); a.remove();
});
tudo();
})();
</script>
</body>
</html>
"""


def gerar_html(lotes: list[Lote], status: list[dict], modo: str) -> str:
    agora = datetime.now()
    dados = {
        "gerado_em": agora.strftime("%d/%m/%Y %H:%M"),
        "modo": modo,
        "categorias": [IMOVEIS, VEICULOS],
        "ordem_modalidades": ORDEM_MODALIDADES,
        "modalidades": DESCRICAO_MODALIDADES,
        "status": status,
        "lotes": [lote_dict(l) for l in lotes],
    }
    # "<" vira \u003c: impede que algum texto dos sites feche a tag <script> ou abra comentário HTML
    js = json.dumps(dados, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    return (HTML_TEMPLATE.replace("__GERADO__", agora.strftime("%d/%m/%Y %H:%M"))
            .replace("__DADOS__", js))


# --------------------------------------------------------------------------------------
# Execução
# --------------------------------------------------------------------------------------
def executar(fontes: list[Fonte], categorias: set[str]) -> tuple[list[Lote], list[dict]]:
    resultados: list[Lote] = []
    status: list[dict] = []

    def roda(f: Fonte):
        t0 = time.time()
        try:
            lotes = [l for l in f.coletar() if l.categoria in categorias]
            return f, lotes, None, time.time() - t0
        except Exception as e:  # noqa: BLE001
            log.debug("Erro em %s", f.nome, exc_info=True)
            return f, [], e, time.time() - t0

    with ThreadPoolExecutor(max_workers=max(1, len(fontes))) as ex:
        for fut in as_completed([ex.submit(roda, f) for f in fontes]):
            f, lotes, erro, seg = fut.result()
            if erro is None:
                log.info("✔ %s: %d lotes (%.0fs)", f.nome, len(lotes), seg)
                status.append({"fonte": f.nome, "chave": f.chave, "ok": True, "n": len(lotes),
                               "segundos": round(seg), "avisos": f.avisos})
                resultados += lotes
            else:
                log.error("✘ %s falhou: %s", f.nome, erro)
                status.append({"fonte": f.nome, "chave": f.chave, "ok": False, "n": 0,
                               "segundos": round(seg), "erro": str(erro), "avisos": f.avisos})
    ordem = [c.chave for c in FONTES]
    status.sort(key=lambda s: ordem.index(s["chave"]) if s["chave"] in ordem else 99)
    return finalizar(resultados), status


def resumo_terminal(lotes: list[Lote], status: list[dict]):
    print("\n" + "=" * 64)
    print("RESUMO")
    print("=" * 64)
    for cat in (IMOVEIS, VEICULOS):
        sel = [l for l in lotes if l.categoria == cat]
        if not sel:
            continue
        print(f"\n{cat}: {len(sel)} lotes")
        cont: dict[str, int] = {}
        for l in sel:
            cont[l.modalidade] = cont.get(l.modalidade, 0) + 1
        for m, n in sorted(cont.items(), key=lambda x: (ORDEM_MODALIDADES.index(x[0]) if x[0] in ORDEM_MODALIDADES else 99, -x[1])):
            print(f"  {m:<34} {n:>6}")
    print("\nFontes:")
    for s in status:
        marca = "ok " if s["ok"] else "ERRO"
        extra = "" if s["ok"] else f" — {s.get('erro')}"
        av = f"  ⚠ {'; '.join(s['avisos'])}" if s.get("avisos") else ""
        print(f"  [{marca}] {s['fonte']:<28} {s['n']:>6} lotes{extra}{av}")
    print()


def main(argv=None):
    ap = argparse.ArgumentParser(description="Coleta leilões de imóveis e veículos em Minas Gerais e gera um relatório HTML.")
    ap.add_argument("--saida", default="leiloes_mg.html", help="arquivo HTML de saída (padrão: leiloes_mg.html)")
    ap.add_argument("--json", default=None, help="também salvar os dados em JSON (padrão: mesmo nome do HTML)")
    ap.add_argument("--sem-json", action="store_true", help="não salvar o JSON")
    ap.add_argument("--categoria", choices=["todas", "imoveis", "veiculos"], default="todas")
    ap.add_argument("--fontes", default="", help="lista separada por vírgula: " + ",".join(c.chave for c in FONTES))
    ap.add_argument("--rapido", action="store_true", help="não abre a página de cada lote (menos detalhes, bem mais rápido)")
    ap.add_argument("--max-detalhes", type=int, default=0, help="limita quantas páginas de lote abrir por fonte (0 = todas)")
    ap.add_argument("--workers", type=int, default=6, help="requisições simultâneas por fonte (padrão: 6)")
    ap.add_argument("--intervalo", type=float, default=0.15, help="intervalo mínimo entre requisições ao mesmo site, em segundos")
    ap.add_argument("--caixa-csv", default="", help="usar um CSV da Caixa baixado manualmente (Lista_imoveis_MG.csv)")
    ap.add_argument("--sodre-todos-patios", action="store_true",
                    help="Sodré Santoro: incluir também os pátios fora de MG (a Sodré não tem pátio em MG)")
    ap.add_argument("--abrir", action="store_true", help="abrir o relatório no navegador ao terminar")
    ap.add_argument("-v", "--verbose", action="store_true", help="mostrar mensagens de depuração")
    args = ap.parse_args(argv)
    for fluxo in (sys.stdout, sys.stderr):  # evita erro de acentos/símbolos em terminais Windows antigos
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    categorias = {"todas": {IMOVEIS, VEICULOS}, "imoveis": {IMOVEIS}, "veiculos": {VEICULOS}}[args.categoria]
    escolhidas = {x.strip().lower() for x in args.fontes.split(",") if x.strip()}
    invalidas = escolhidas - {c.chave for c in FONTES}
    if invalidas:
        ap.error(f"fonte(s) desconhecida(s): {', '.join(sorted(invalidas))}")
    cfg = Config(detalhes=not args.rapido, workers=args.workers, max_detalhes=args.max_detalhes, caixa_csv=args.caixa_csv,
                 sodre_todos=args.sodre_todos_patios)
    http = Http(intervalo=args.intervalo)
    fontes = [c(http, cfg) for c in FONTES
              if (not escolhidas or c.chave in escolhidas) and set(c.categorias) & categorias]
    if not fontes:
        ap.error("nenhuma fonte selecionada para essa categoria")

    log.info("Coletando %s em %d fontes: %s%s", " e ".join(sorted(categorias)), len(fontes),
             ", ".join(f.nome for f in fontes), " (modo rápido)" if args.rapido else "")
    t0 = time.time()
    lotes, status = executar(fontes, categorias)

    saida = Path(args.saida)
    modo = "modo rápido (sem a página de cada lote)" if args.rapido else ""
    saida.write_text(gerar_html(lotes, status, modo), encoding="utf-8")
    if not args.sem_json:
        caminho_json = Path(args.json) if args.json else saida.with_suffix(".json")
        caminho_json.write_text(json.dumps({"gerado_em": datetime.now().isoformat(timespec="seconds"),
                                            "status": status, "lotes": [lote_dict(l) for l in lotes]},
                                           ensure_ascii=False, indent=1), encoding="utf-8")
    resumo_terminal(lotes, status)
    print(f"Relatório: {saida.resolve()}  ({len(lotes)} lotes, {time.time() - t0:.0f}s)")
    if args.abrir:
        webbrowser.open(saida.resolve().as_uri())
    return 0 if any(s["ok"] for s in status) else 1


if __name__ == "__main__":
    sys.exit(main())
