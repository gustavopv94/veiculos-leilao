"""Transforma um Lote (formato livre de cada fonte) em campos estruturados de veículo."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from leiloes_mg import M_DETRAN, M_GOV, M_JUDICIAL, Lote, num_br, sem_acento

# ordem do "pior" para o "melhor" — quando há sinais conflitantes vale o pior
CONDICOES = ["Sucata", "Grande monta", "Média monta", "Pequena monta", "Sinistrado", "Conservado", "Não informado"]
TIPOS = ["Carro / utilitário", "Moto", "Caminhão / reboque", "Ônibus", "Trator / máquina", "Outros"]
ORIGENS = ["Financeira / banco", "Seguradora", "Judicial", "Órgão público", "Locadora / frota", "Empresa", "Outros"]

_REGRAS_COND = [
    ("Sucata", re.compile(r"sucata|irrecuperavel|inservivel|baixa definitiva|so para (desmonte|pecas)|para desmontagem")),
    ("Grande monta", re.compile(r"grande monta")),
    ("Média monta", re.compile(r"media monta")),
    ("Pequena monta", re.compile(r"pequena monta")),
    ("Sinistrado", re.compile(r"(?<!sem )(?<!nao possui )sinistr|colisao|alagad|enchente|incendi|capotad")),
    ("Conservado", re.compile(r"conservad|sem sinistro|documentavel|apto a circular|circulacao|recuperado de financiamento|"
                              r"recuperado financiamento|seminovo|^normal$|^usados?$|nao aplicavel|"
                              r"^financiamento$|^uso normal$|^frota$|^consorcio$|^inteiro$")),
]
_CHAVES_COND = ("Monta", "Condição", "Sinistro", "Situação do veículo", "Situação veículo", "Situação", "Procedência",
                "Classificação do dano", "Tipo de perda", "Subcategoria")
_RE_ANO2 = re.compile(r"\b((?:19[5-9]|20[0-4])\d)\s*/\s*((?:19[5-9]|20[0-4])\d)\b")
_RE_ANO1 = re.compile(r"\b((?:19[5-9]|20[0-4])\d)\b")
_RE_KM_ROTULO = re.compile(r"(?:quilometragem|hod[oô]metro|od[oô]metro|km\s*rodados?|km\s*atual)\s*[:\-]?\s*(\d{1,3}(?:\.\d{3})+|\d+)", re.I)
_RE_KM_SUFIXO = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d{3,7})\s*(?:km|kms|quil[oô]metros)\b", re.I)

MARCAS = {
    "VW": "VOLKSWAGEN", "VOLKS": "VOLKSWAGEN", "VOLKSWAGEN": "VOLKSWAGEN", "GM": "CHEVROLET", "CHEVROLET": "CHEVROLET",
    "CHEV": "CHEVROLET", "FIAT": "FIAT", "FORD": "FORD", "RENAULT": "RENAULT", "HONDA": "HONDA", "TOYOTA": "TOYOTA",
    "HYUNDAI": "HYUNDAI", "NISSAN": "NISSAN", "PEUGEOT": "PEUGEOT", "CITROEN": "CITROËN", "CITROËN": "CITROËN",
    "JEEP": "JEEP", "MITSUBISHI": "MITSUBISHI", "MMC": "MITSUBISHI", "KIA": "KIA", "BMW": "BMW", "AUDI": "AUDI",
    "MERCEDES": "MERCEDES-BENZ", "MBENZ": "MERCEDES-BENZ", "M.BENZ": "MERCEDES-BENZ", "MERCEDES-BENZ": "MERCEDES-BENZ",
    "MERCEDESBENZ": "MERCEDES-BENZ", "YAMAHA": "YAMAHA", "SUZUKI": "SUZUKI", "KAWASAKI": "KAWASAKI", "DAFRA": "DAFRA",
    "SHINERAY": "SHINERAY", "TRAXX": "TRAXX", "SUNDOWN": "SUNDOWN", "HARLEY": "HARLEY-DAVIDSON",
    "HARLEY-DAVIDSON": "HARLEY-DAVIDSON", "TRIUMPH": "TRIUMPH", "DUCATI": "DUCATI", "BMW MOTORRAD": "BMW",
    "VOLVO": "VOLVO", "SCANIA": "SCANIA", "IVECO": "IVECO", "DAF": "DAF", "MAN": "MAN", "AGRALE": "AGRALE",
    "LAND": "LAND ROVER", "LAND ROVER": "LAND ROVER", "LANDROVER": "LAND ROVER", "JAGUAR": "JAGUAR", "PORSCHE": "PORSCHE",
    "CHERY": "CAOA CHERY", "CAOA": "CAOA CHERY", "JAC": "JAC", "LIFAN": "LIFAN", "BYD": "BYD", "GWM": "GWM",
    "HAVAL": "GWM", "RAM": "RAM", "DODGE": "DODGE", "CHRYSLER": "CHRYSLER", "SUBARU": "SUBARU", "LEXUS": "LEXUS",
    "MINI": "MINI", "SMART": "SMART", "VOLARE": "VOLARE", "MARCOPOLO": "MARCOPOLO", "TROLLER": "TROLLER",
    "EFFA": "EFFA", "HAOJUE": "HAOJUE", "KASINSKI": "KASINSKI", "BRAVAX": "BRAVAX", "JTA": "JTA", "AVELLOZ": "AVELLOZ",
    "MOTTU": "MOTTU", "IMP": "", "I": "",
}


def _txt(s) -> str:
    return sem_acento(str(s or "")).lower().strip()


def _int(s) -> int | None:
    v = num_br(s) if isinstance(s, str) else s
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _det(l: Lote, *chaves: str) -> str:
    for k in chaves:
        if l.detalhes.get(k):
            return str(l.detalhes[k])
    return ""


def _pior(textos) -> str | None:
    achados = set()
    for t in textos:
        t = _txt(t)
        if not t:
            continue
        for nome, rx in _REGRAS_COND:
            if rx.search(t):
                achados.add(nome)
    if not achados:
        return None
    return min(achados, key=CONDICOES.index)


def condicao(l: Lote) -> str:
    estruturado = [l.detalhes.get(k) for k in _CHAVES_COND] + list(l.selos)
    # a classificação de monta, quando existe, manda (ex.: Copart "Irrecuperável" + "Grande monta" = Grande monta),
    # a não ser que algum campo diga explicitamente que é sucata
    monta = _pior([l.detalhes.get("Monta"), l.detalhes.get("Classificação do dano")])
    if monta and not any("sucata" in _txt(x) for x in estruturado):
        return monta
    return _pior(estruturado) or _pior([l.titulo, l.descricao[:2000]]) or "Não informado"


def anos(l: Lote) -> tuple[int | None, int | None]:
    v = _det(l, "Ano fab./modelo")
    if m := _RE_ANO2.search(v):
        return int(m[1]), int(m[2])
    fab = _det(l, "Ano de Fabricação", "Ano Fabricação", "Ano fabricação")
    mod = _det(l, "Ano do Modelo", "Ano Modelo", "Ano modelo")
    f, m_ = (_RE_ANO1.search(fab), _RE_ANO1.search(mod))
    if f or m_:
        return (int(f[1]) if f else None), (int(m_[1]) if m_ else (int(f[1]) if f else None))
    ano = _det(l, "Ano")
    if m := _RE_ANO2.search(ano):
        return int(m[1]), int(m[2])
    if m := _RE_ANO1.search(ano):
        return int(m[1]), int(m[1])
    if m := _RE_ANO2.search(l.titulo) or _RE_ANO2.search(l.descricao[:600]):
        return int(m[1]), int(m[2])
    if m := _RE_ANO1.search(l.titulo):
        return None, int(m[1])
    return None, None


def quilometragem(l: Lote) -> int | None:
    v = _det(l, "Quilometragem", "KM", "Km", "Odômetro", "Hodômetro", "Quilometragem informada")
    if v and re.search(r"\d", v):
        bruto = re.split(r"km", v, flags=re.I)[0].split(",")[0]  # "12.345 km" / "12.345,0"
        n = _int(re.sub(r"\D", "", bruto) or None)
        if n is not None and 0 < n < 3_000_000:
            return n
    for rx in (_RE_KM_ROTULO, _RE_KM_SUFIXO):
        if m := rx.search(l.descricao[:4000]):
            n = _int(m[1].replace(".", ""))
            if n is not None and 10 <= n < 3_000_000:
                return n
    return None


def marca_modelo(l: Lote) -> tuple[str, str]:
    marca, modelo = _det(l, "Marca").upper(), _det(l, "Modelo").upper()
    mm = _det(l, "Marca/Modelo", "Marca / Modelo", "Veículo").upper()
    if mm and (not marca or not modelo):
        partes = re.split(r"\s*[/\-]\s*", mm, maxsplit=2)
        if partes and partes[0] in ("I", "IMP"):  # "I/FIAT ..." = importado
            partes = partes[1:]
        if partes:
            marca = marca or partes[0]
            modelo = modelo or " ".join(partes[1:]).strip()
    if not marca:
        palavras = re.findall(r"[A-ZÀ-Ü][A-ZÀ-Ü.\-]+", (l.titulo or "").upper())
        for i, w in enumerate(palavras[:4]):
            if w.strip(".-") in MARCAS and MARCAS[w.strip(".-")]:
                marca = w
                modelo = modelo or " ".join(palavras[i + 1:i + 3])
                break
    k = re.sub(r"\s+", " ", marca.strip(" .-/"))
    marca = MARCAS.get(k, MARCAS.get(k.replace(" ", ""), k))
    modelo = re.sub(r"\s+", " ", re.sub(r"\b(19|20)\d{2}\s*/\s*(19|20)\d{2}\b", "", modelo)).strip(" -/")
    return marca, modelo[:60]


def tipo(l: Lote) -> str:
    t = _txt(l.tipo) + " " + _txt(l.detalhes.get("Categoria")) + " " + _txt(l.detalhes.get("Subcategoria"))
    if re.search(r"onibus|micro.?onibus", t):
        return "Ônibus"
    if re.search(r"\bmoto|motociclet|motoneta|ciclomotor|triciclo|quadriciclo", t):
        return "Moto"
    if re.search(r"caminh|reboque|cavalo|carreta|semi.?reboque|implemento|pesad", t):
        return "Caminhão / reboque"
    if re.search(r"trator|maquin|agric|empilhad|retroescav", t):
        return "Trator / máquina"
    if re.search(r"carro|utilit|sedan|hatch|picape|suv|automov|perua|van|furg|camionet|sinistrad|veiculo", t):
        return "Carro / utilitário"
    return "Outros" if t.strip() else "Carro / utilitário"


def origem(l: Lote) -> str:
    if l.modalidade == M_JUDICIAL:
        return "Judicial"
    if l.modalidade in (M_GOV, M_DETRAN):
        return "Órgão público"
    t = " ".join(_txt(x) for x in (l.detalhes.get("Origem"), l.detalhes.get("Procedência"), l.detalhes.get("Tipo de perda"),
                                   l.comitente, " ".join(l.selos)))
    if re.search(r"penhor|judicial", t):
        return "Judicial"
    if re.search(r"segur|associac|protecao veicular|sinistro", t):
        return "Seguradora"
    if re.search(r"banco|financ|consorcio|leasing|credito|bradesco|itau|santander|safra|\bpan\b|omni|aymore|bv\b", t):
        return "Financeira / banco"
    if re.search(r"locadora|localiza|movida|unidas|frota|rent a car|aluguel", t):
        return "Locadora / frota"
    if re.search(r"prefeitura|secretaria|governo|munic|detran|policia|tribunal|exercito|batalh|\bder\b|autarquia|apreens|patrim", t):
        return "Órgão público"
    if re.search(r"empresa|ltda|s\.?a\b|eireli|\bme\b", t):
        return "Empresa"
    return "Outros"


def _limpa_valor(s: str) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip())
    return s.title() if s.isupper() or s.islower() else s


def combustivel(l: Lote) -> str:
    t = _txt(_det(l, "Combustível", "Combustivel"))
    if not t:
        return ""
    if re.search(r"flex|alc.*gas|gas.*alc|alcool/gasolina", t):
        return "Flex"
    for k, v in (("diesel", "Diesel"), ("eletric", "Elétrico"), ("hibrid", "Híbrido"), ("gnv", "GNV"),
                 ("gasolina", "Gasolina"), ("alcool", "Etanol"), ("etanol", "Etanol")):
        if k in t:
            return v
    return _limpa_valor(t)


def cambio(l: Lote) -> str:
    t = _txt(_det(l, "Câmbio", "Cambio", "Transmissão"))
    if not t:
        t = "automatico" if re.search(r"\bcambio automatico\b|\bautomatic", _txt(_det(l, "Checklist", "Opcionais"))) else ""
    return "Automático" if "autom" in t else "Manual" if "manual" in t or "mecan" in t else ""


def id_lote(chave_fonte: str, l: Lote) -> str:
    base = l.id_fonte or hashlib.sha1(l.link.encode()).hexdigest()[:12]
    return f"{chave_fonte}:{base}"


def para_linha(chave_fonte: str, l: Lote, agora: str) -> dict:
    fab, mod = anos(l)
    marca, modelo = marca_modelo(l)
    final = _det(l, "Final placa", "Final da placa")
    return {
        "id": id_lote(chave_fonte, l), "fonte": l.fonte, "fonte_chave": chave_fonte, "titulo": l.titulo[:300],
        "link": l.link, "marca": marca, "modelo": modelo, "ano_fab": fab, "ano_modelo": mod,
        "km": quilometragem(l), "condicao": condicao(l), "combustivel": combustivel(l), "cambio": cambio(l),
        "cor": _limpa_valor(_det(l, "Cor")), "final_placa": (re.search(r"\d", final) or [""])[0] if final else "",
        "tipo": tipo(l), "origem": origem(l), "modalidade": l.modalidade, "uf": (l.uf or "").upper(),
        "cidade": l.cidade, "endereco": l.endereco, "valor_avaliacao": l.valor_avaliacao,
        "valor_minimo": l.valor_minimo, "lance_atual": l.lance_atual, "preco_ref": l.preco_ref,
        "desconto": l.desconto, "data_ref": l.data_ref, "data_encerramento": l.data_encerramento,
        "status": l.status, "comitente": l.comitente, "leiloeiro": l.leiloeiro,
        "modalidade_original": l.modalidade_original, "foto": l.fotos[0] if l.fotos else "",
        "fotos": json.dumps(l.fotos[:40], ensure_ascii=False),
        "detalhes": json.dumps(l.detalhes, ensure_ascii=False),
        "pracas": json.dumps(l.pracas, ensure_ascii=False, default=str),
        "documentos": json.dumps(l.documentos, ensure_ascii=False),
        "selos": json.dumps(l.selos, ensure_ascii=False), "descricao": l.descricao[:8000], "visto_em": agora,
    }


def relatorio_preenchimento(linhas: list[dict]) -> str:
    campos = ("ano_modelo", "km", "condicao", "marca", "uf", "preco_ref", "foto")
    por_fonte: dict[str, list[dict]] = {}
    for r in linhas:
        por_fonte.setdefault(r["fonte"], []).append(r)
    out = [f"{'fonte':<26}{'lotes':>7}  " + "".join(f"{c:>11}" for c in campos)]
    for f, rs in sorted(por_fonte.items(), key=lambda kv: -len(kv[1])):
        cel = []
        for c in campos:
            ok = sum(1 for r in rs if r[c] not in (None, "", "Não informado"))
            cel.append(f"{100 * ok / len(rs):>10.0f}%")
        out.append(f"{f[:25]:<26}{len(rs):>7}  " + "".join(cel))
    return "\n".join(out)


def agora_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
