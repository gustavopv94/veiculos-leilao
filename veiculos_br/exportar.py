"""Exporta o banco para um site estático (GitHub Pages): lista enxuta + detalhes em 256 pedaços + páginas."""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from . import banco

WEB = Path(__file__).resolve().parent / "web"
CAMPOS_LISTA = ["id", "fonte", "titulo", "link", "marca", "modelo", "ano_fab", "ano_modelo", "km", "condicao", "tipo",
                "origem", "uf", "cidade", "preco_ref", "valor_avaliacao", "lance_atual", "desconto", "data_ref",
                "data_encerramento", "status", "foto", "combustivel", "cambio", "modalidade"]
CAMPOS_DET = ["descricao", "detalhes", "fotos", "pracas", "documentos", "endereco", "comitente", "leiloeiro",
              "modalidade_original", "valor_minimo", "cor", "final_placa", "selos", "visto_em"]
JSON_COLS = {"detalhes", "fotos", "pracas", "documentos", "selos"}


def pedaco(id_: str) -> str:
    """Hash djb2 (o mesmo do api_local.js) → '00'..'ff'."""
    h = 5381
    for ch in id_.encode("utf-8"):
        h = ((h * 33) ^ ch) & 0xFFFFFFFF
    return f"{h & 0xFF:02x}"


def exportar(caminho_banco, saida: Path, repo: str = "") -> dict:
    saida = Path(saida)
    if saida.exists():
        shutil.rmtree(saida)
    (saida / "dados" / "det").mkdir(parents=True)
    con = banco.conectar(caminho_banco)
    rows = con.execute("SELECT * FROM lotes WHERE ativo=1 ORDER BY id").fetchall()
    lista = [[r[c] for c in CAMPOS_LISTA] for r in rows]
    _grava(saida / "dados" / "lista.json", {"cols": CAMPOS_LISTA, "rows": lista})
    pedacos: dict[str, dict] = {}
    for r in rows:
        d = {}
        for c in CAMPOS_DET:
            v = r[c]
            if c in JSON_COLS and isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    pass
            if v not in (None, "", [], {}):
                d[c] = v
        pedacos.setdefault(pedaco(r["id"]), {})[r["id"]] = d
    for p, conteudo in pedacos.items():
        _grava(saida / "dados" / "det" / f"{p}.json", conteudo)
    st = banco.status(con)
    meta = {"gerado_em": datetime.now().isoformat(timespec="seconds"), "ativos": len(rows),
            "fontes": st["fontes"], "repo": repo}
    _grava(saida / "dados" / "meta.json", meta)
    # páginas: mesmo front-end do site local + camada que responde a "API" no próprio navegador
    for arq in ("app.js", "estilo.css", "api_local.js"):
        shutil.copy2(WEB / arq, saida / arq)
    html = (WEB / "index.html").read_text(encoding="utf-8")
    html = html.replace('href="/static/estilo.css"', 'href="estilo.css"')
    html = html.replace('<script src="/static/app.js"></script>',
                        f'<script>window.MODO_ESTATICO = true; window.REPO = {json.dumps(repo)};</script>\n'
                        '<script src="api_local.js"></script>\n<script src="app.js"></script>')
    (saida / "index.html").write_text(html, encoding="utf-8")
    (saida / ".nojekyll").write_text("", encoding="utf-8")
    return meta


def _grava(caminho: Path, obj):
    caminho.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), default=str), encoding="utf-8")
