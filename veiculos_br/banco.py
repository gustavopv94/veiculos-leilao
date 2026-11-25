"""Armazenamento em SQLite (com busca textual FTS5) e consultas com filtros/facetas."""
from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from . import RAIZ

BANCO_PADRAO = RAIZ / "veiculos.db"

COLUNAS = ["id", "fonte", "fonte_chave", "titulo", "link", "marca", "modelo", "ano_fab", "ano_modelo", "km", "condicao",
           "combustivel", "cambio", "cor", "final_placa", "tipo", "origem", "modalidade", "uf", "cidade", "endereco",
           "valor_avaliacao", "valor_minimo", "lance_atual", "preco_ref", "desconto", "data_ref", "data_encerramento",
           "status", "comitente", "leiloeiro", "modalidade_original", "foto", "fotos", "detalhes", "pracas",
           "documentos", "selos", "descricao", "visto_em"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS lotes (
  id TEXT PRIMARY KEY, fonte TEXT, fonte_chave TEXT, titulo TEXT, link TEXT, marca TEXT, modelo TEXT,
  ano_fab INTEGER, ano_modelo INTEGER, km INTEGER, condicao TEXT, combustivel TEXT, cambio TEXT, cor TEXT,
  final_placa TEXT, tipo TEXT, origem TEXT, modalidade TEXT, uf TEXT, cidade TEXT, endereco TEXT,
  valor_avaliacao REAL, valor_minimo REAL, lance_atual REAL, preco_ref REAL, desconto REAL, data_ref TEXT,
  data_encerramento TEXT, status TEXT, comitente TEXT, leiloeiro TEXT, modalidade_original TEXT, foto TEXT,
  fotos TEXT, detalhes TEXT, pracas TEXT, documentos TEXT, selos TEXT, descricao TEXT,
  visto_em TEXT, primeiro_visto TEXT, ativo INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_ativo ON lotes(ativo);
CREATE INDEX IF NOT EXISTS ix_uf ON lotes(uf);
CREATE INDEX IF NOT EXISTS ix_cond ON lotes(condicao);
CREATE INDEX IF NOT EXISTS ix_ano ON lotes(ano_modelo);
CREATE INDEX IF NOT EXISTS ix_km ON lotes(km);
CREATE INDEX IF NOT EXISTS ix_preco ON lotes(preco_ref);
CREATE INDEX IF NOT EXISTS ix_data ON lotes(data_ref);
CREATE INDEX IF NOT EXISTS ix_fonte ON lotes(fonte_chave);
CREATE INDEX IF NOT EXISTS ix_marca ON lotes(marca);
CREATE INDEX IF NOT EXISTS ix_tipo ON lotes(tipo);
CREATE VIRTUAL TABLE IF NOT EXISTS lotes_fts USING fts5(
  titulo, marca, modelo, cidade, descricao, content='lotes', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS favoritos (
  id TEXT PRIMARY KEY, criado_em TEXT, lembrar INTEGER DEFAULT 0, antecedencia_min INTEGER DEFAULT 60, nota TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS lembretes_enviados (
  id TEXT, data_ref TEXT, antecedencia_min INTEGER, enviado_em TEXT, canais TEXT,
  PRIMARY KEY (id, data_ref, antecedencia_min)
);
CREATE TABLE IF NOT EXISTS coletas (
  quando TEXT, fonte TEXT, fonte_chave TEXT, ok INTEGER, n INTEGER, segundos INTEGER, erro TEXT, avisos TEXT
);
"""


def conectar(caminho: Path | str = BANCO_PADRAO) -> sqlite3.Connection:
    con = sqlite3.connect(str(caminho), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def salvar(con: sqlite3.Connection, linhas: list[dict], status: list[dict], agora: str):
    """Upsert dos lotes; lotes de fontes que rodaram com sucesso e não apareceram ficam inativos."""
    cols = COLUNAS
    sql = (f"INSERT INTO lotes ({','.join(cols)}, primeiro_visto, ativo) VALUES ({','.join('?' * len(cols))}, ?, 1) "
           f"ON CONFLICT(id) DO UPDATE SET " + ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id") + ", ativo=1")
    with con:
        vistos = set()
        for r in linhas:
            if r["id"] in vistos:
                continue
            vistos.add(r["id"])
            con.execute(sql, [r.get(c) for c in cols] + [agora])
        for s in status:
            if s["ok"] and s["n"] > 0:
                con.execute("UPDATE lotes SET ativo=0 WHERE fonte_chave=? AND visto_em<?", (s["chave"], agora))
            con.execute("INSERT INTO coletas VALUES (?,?,?,?,?,?,?,?)",
                        (agora, s["fonte"], s["chave"], int(s["ok"]), s["n"], s.get("segundos", 0), s.get("erro", ""),
                         json.dumps(s.get("avisos") or [], ensure_ascii=False)))
        con.execute("INSERT INTO lotes_fts(lotes_fts) VALUES('rebuild')")


# --------------------------------------------------------------------------------------
# Consultas
# --------------------------------------------------------------------------------------
LISTA_CAMPOS = ["ativo", "id", "fonte", "titulo", "link", "marca", "modelo", "ano_fab", "ano_modelo", "km", "condicao", "tipo",
                "origem", "uf", "cidade", "preco_ref", "valor_avaliacao", "lance_atual", "desconto", "data_ref",
                "data_encerramento", "status", "foto", "combustivel", "cambio", "modalidade"]

FACETAS = {"condicao": "condicao", "uf": "uf", "tipo": "tipo", "fonte": "fonte", "marca": "marca", "origem": "origem",
           "combustivel": "combustivel", "cambio": "cambio", "cidade": "cidade"}

ORDENS = {
    "encerra": "CASE WHEN data_ref IS NULL OR data_ref='' THEN 1 ELSE 0 END, data_ref ASC",
    "preco_asc": "CASE WHEN preco_ref IS NULL THEN 1 ELSE 0 END, preco_ref ASC",
    "preco_desc": "preco_ref DESC",
    "ano_desc": "CASE WHEN ano_modelo IS NULL THEN 1 ELSE 0 END, ano_modelo DESC",
    "ano_asc": "CASE WHEN ano_modelo IS NULL THEN 1 ELSE 0 END, ano_modelo ASC",
    "km_asc": "CASE WHEN km IS NULL THEN 1 ELSE 0 END, km ASC",
    "desconto": "CASE WHEN desconto IS NULL THEN 1 ELSE 0 END, desconto DESC",
    "recentes": "primeiro_visto DESC",
}


def _lista(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        itens = [x for s in v for x in str(s).split("|")]
    else:
        itens = str(v).split("|")
    return [x.strip() for x in itens if x.strip()]


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def _fts(q: str) -> str:
    q = unicodedata.normalize("NFKD", q).encode("ascii", "ignore").decode()
    termos = re.findall(r"[\w]+", q)
    return " AND ".join(f'"{t}"*' for t in termos[:12])


def montar_where(f: dict, ignorar: str = "") -> tuple[str, list]:
    so_fav = str(f.get("fav", "")) in ("1", "true")
    # nos favoritos mostra também lotes que já saíram das fontes (para não sumirem sem aviso)
    cond, args = (["l.id IN (SELECT id FROM favoritos)"] if so_fav else ["l.ativo=1"]), []
    if str(f.get("lembrar", "")) in ("1", "true"):
        cond.append("l.id IN (SELECT id FROM favoritos WHERE lembrar=1)")
    for chave, col in FACETAS.items():
        if chave == ignorar:
            continue
        vals = _lista(f.get(chave))
        if vals:
            cond.append(f"COALESCE(l.{col},'') IN ({','.join('?' * len(vals))})")
            args += ["" if v == "(vazio)" else v for v in vals]
    if q := (f.get("q") or "").strip():
        if fq := _fts(q):
            cond.append("l.rowid IN (SELECT rowid FROM lotes_fts WHERE lotes_fts MATCH ?)")
            args.append(fq)
    if modelo := (f.get("modelo") or "").strip():
        cond.append("l.modelo LIKE ?")
        args.append(f"%{modelo}%")
    ano = "COALESCE(l.ano_modelo, l.ano_fab)"
    if (v := _num(f.get("ano_min"))) is not None:
        cond.append(f"{ano} >= ?"); args.append(v)
    if (v := _num(f.get("ano_max"))) is not None:
        cond.append(f"{ano} <= ?"); args.append(v)
    sem_km = str(f.get("sem_km", "1")) not in ("0", "false", "")
    if (v := _num(f.get("km_min"))) is not None:
        cond.append("(l.km >= ?" + (" OR l.km IS NULL)" if sem_km else ")")); args.append(v)
    if (v := _num(f.get("km_max"))) is not None:
        cond.append("(l.km <= ?" + (" OR l.km IS NULL)" if sem_km else ")")); args.append(v)
    if (v := _num(f.get("preco_min"))) is not None:
        cond.append("l.preco_ref >= ?"); args.append(v)
    if (v := _num(f.get("preco_max"))) is not None:
        cond.append("l.preco_ref <= ?"); args.append(v)
    if (v := _num(f.get("dias"))) is not None:
        agora = datetime.now()
        cond.append("l.data_ref >= ? AND l.data_ref <= ?")
        args += [agora.isoformat(timespec="seconds"), (agora + timedelta(days=v)).isoformat(timespec="seconds")]
    if str(f.get("so_foto", "")) in ("1", "true"):
        cond.append("COALESCE(l.foto,'') <> ''")
    if str(f.get("com_km", "")) in ("1", "true"):
        cond.append("l.km IS NOT NULL")
    return " AND ".join(cond), args


def buscar(con, f: dict, pagina: int = 1, por_pagina: int = 48) -> dict:
    where, args = montar_where(f)
    total = con.execute(f"SELECT COUNT(*) FROM lotes l WHERE {where}", args).fetchone()[0]
    ordem = ORDENS.get(f.get("ordem") or "encerra", ORDENS["encerra"])
    por_pagina = max(1, min(int(por_pagina), 500))
    rows = con.execute(f"SELECT {','.join('l.' + c for c in LISTA_CAMPOS)} FROM lotes l WHERE {where} "
                       f"ORDER BY {ordem}, l.id LIMIT ? OFFSET ?", args + [por_pagina, (max(1, pagina) - 1) * por_pagina]).fetchall()
    itens = [dict(r) for r in rows]
    marcar_favoritos(con, itens)
    return {"total": total, "pagina": pagina, "por_pagina": por_pagina, "itens": itens}


def facetas(con, f: dict) -> dict:
    out = {}
    for chave, col in FACETAS.items():
        if chave == "cidade" and not _lista(f.get("uf")):
            continue  # cidades só depois de escolher a UF
        where, args = montar_where(f, ignorar=chave)
        lim = 400 if chave in ("marca", "cidade") else 100
        rows = con.execute(f"SELECT COALESCE(l.{col},'') v, COUNT(*) n FROM lotes l WHERE {where} "
                           f"GROUP BY v ORDER BY n DESC LIMIT {lim}", args).fetchall()
        out[chave] = [{"v": r["v"] or "(vazio)", "n": r["n"]} for r in rows]
    where, args = montar_where(f)
    r = con.execute(f"SELECT MIN(COALESCE(ano_modelo,ano_fab)) a0, MAX(COALESCE(ano_modelo,ano_fab)) a1, "
                    f"MAX(km) k1, MAX(preco_ref) p1 FROM lotes l WHERE {where}", args).fetchone()
    out["faixas"] = dict(r)
    return out


def lote(con, id_: str) -> dict | None:
    r = con.execute("SELECT * FROM lotes WHERE id=?", (id_,)).fetchone()
    if r is None:
        return None
    d = dict(r)
    for k in ("fotos", "detalhes", "pracas", "documentos", "selos"):
        try:
            d[k] = json.loads(d[k] or "null")
        except ValueError:
            pass
    marcar_favoritos(con, [d])
    return d


# --------------------------------------------------------------------------------------
# Favoritos e lembretes
# --------------------------------------------------------------------------------------
def marcar_favoritos(con, itens: list[dict]):
    if not itens:
        return
    ids = [i["id"] for i in itens]
    favs = {r["id"]: dict(r) for r in con.execute(
        f"SELECT * FROM favoritos WHERE id IN ({','.join('?' * len(ids))})", ids)}
    for i in itens:
        f = favs.get(i["id"])
        i["fav"] = bool(f)
        i["lembrar"] = bool(f and f["lembrar"])
        i["antecedencia_min"] = f["antecedencia_min"] if f else None
        i["nota"] = f["nota"] if f else ""


def favoritos(con) -> list[dict]:
    return [dict(r) for r in con.execute("SELECT * FROM favoritos ORDER BY criado_em DESC")]


def salvar_favorito(con, id_: str, lembrar: bool = False, antecedencia_min: int = 60, nota: str = "") -> dict:
    agora = datetime.now().isoformat(timespec="seconds")
    with con:
        con.execute("INSERT INTO favoritos (id, criado_em, lembrar, antecedencia_min, nota) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET lembrar=excluded.lembrar, antecedencia_min=excluded.antecedencia_min, "
                    "nota=excluded.nota", (id_, agora, int(bool(lembrar)), int(antecedencia_min or 60), nota or ""))
    return dict(con.execute("SELECT * FROM favoritos WHERE id=?", (id_,)).fetchone())


def remover_favorito(con, id_: str):
    with con:
        con.execute("DELETE FROM favoritos WHERE id=?", (id_,))


def lembretes_pendentes(con, agora: datetime | None = None) -> list[dict]:
    """Favoritos com lembrete cujo leilão começa dentro da antecedência escolhida e que ainda não foram avisados."""
    agora = agora or datetime.now()
    rows = con.execute(
        "SELECT f.id, f.antecedencia_min, f.nota, l.titulo, l.link, l.data_ref, l.data_encerramento, l.preco_ref, "
        "l.cidade, l.uf, l.fonte, l.condicao FROM favoritos f JOIN lotes l ON l.id=f.id "
        "WHERE f.lembrar=1 AND COALESCE(l.data_ref,'')<>'' AND l.data_ref>? "
        "AND NOT EXISTS (SELECT 1 FROM lembretes_enviados e WHERE e.id=f.id AND e.data_ref=l.data_ref "
        "AND e.antecedencia_min=f.antecedencia_min)", (agora.isoformat(timespec="seconds"),)).fetchall()
    out = []
    for r in rows:
        try:
            quando = datetime.fromisoformat(r["data_ref"])
        except ValueError:
            continue
        if quando - timedelta(minutes=r["antecedencia_min"]) <= agora:
            out.append(dict(r))
    return out


def registrar_lembrete(con, item: dict, canais: str):
    with con:
        con.execute("INSERT OR IGNORE INTO lembretes_enviados VALUES (?,?,?,?,?)",
                    (item["id"], item["data_ref"], item["antecedencia_min"],
                     datetime.now().isoformat(timespec="seconds"), canais))


def status(con) -> dict:
    ult = con.execute("SELECT MAX(quando) FROM coletas").fetchone()[0]
    fontes = [dict(r) for r in con.execute(
        "SELECT c.* FROM coletas c JOIN (SELECT fonte_chave, MAX(quando) q FROM coletas GROUP BY fonte_chave) u "
        "ON c.fonte_chave=u.fonte_chave AND c.quando=u.q ORDER BY c.fonte")]
    for s in fontes:
        s["avisos"] = json.loads(s["avisos"] or "[]")
    ativos = con.execute("SELECT COUNT(*) FROM lotes WHERE ativo=1").fetchone()[0]
    return {"ultima_coleta": ult, "ativos": ativos, "fontes": fontes}
