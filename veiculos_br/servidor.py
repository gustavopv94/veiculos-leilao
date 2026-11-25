"""Servidor local: API JSON de busca + página web."""
from __future__ import annotations

import csv
import io
import logging
import threading
import time
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import RAIZ, acesso, banco, lembretes

log = logging.getLogger("veiculos_br")

WEB = Path(__file__).resolve().parent / "web"


def criar_app(caminho_banco=banco.BANCO_PADRAO) -> FastAPI:
    app = FastAPI(title="Veículos em leilão — Brasil")
    con = banco.conectar(caminho_banco)

    @app.middleware("http")
    async def proteger(request: Request, call_next):
        if acesso.pedido_externo(request.headers, request.client.host if request.client else ""):
            if not acesso.autorizado(request.headers.get("authorization")):
                msg = "Senha necessária." if acesso.carregar() else "Acesso externo desligado: defina uma senha no PC."
                return PlainTextResponse(msg, status_code=401,
                                         headers={"WWW-Authenticate": 'Basic realm="Veiculos em leilao", charset="UTF-8"'})
        return await call_next(request)

    def filtros(req: Request) -> dict:
        return dict(req.query_params)

    @app.get("/api/lotes")
    def api_lotes(request: Request, pagina: int = 1, por_pagina: int = 48):
        return banco.buscar(con, filtros(request), pagina, por_pagina)

    @app.get("/api/facetas")
    def api_facetas(request: Request):
        return banco.facetas(con, filtros(request))

    @app.get("/api/lotes/{id_:path}")
    def api_lote(id_: str):
        d = banco.lote(con, id_)
        if d is None:
            raise HTTPException(404, "lote não encontrado")
        return d

    @app.get("/api/favoritos")
    def api_favoritos():
        return banco.favoritos(con)

    @app.put("/api/favoritos/{id_:path}")
    def api_favoritar(id_: str, dados: dict = Body(default={})):
        if con.execute("SELECT 1 FROM lotes WHERE id=?", (id_,)).fetchone() is None:
            raise HTTPException(404, "lote não encontrado")
        return banco.salvar_favorito(con, id_, bool(dados.get("lembrar")), int(dados.get("antecedencia_min") or 60),
                                     str(dados.get("nota") or "")[:500])

    @app.delete("/api/favoritos/{id_:path}")
    def api_desfavoritar(id_: str):
        banco.remover_favorito(con, id_)
        return {"ok": True}

    @app.get("/api/lembretes/config")
    def api_cfg():
        cfg = lembretes.carregar_config()
        t, em = cfg.get("telegram") or {}, cfg.get("email") or {}
        return {"email": lembretes.email_configurado(cfg), "email_usuario": em.get("usuario", ""),
                "email_para": em.get("para", ""),
                "telegram": lembretes.telegram_configurado(cfg), "telegram_nome": t.get("nome", ""),
                "whatsapp": lembretes.whatsapp_configurado(cfg), "windows": cfg.get("windows", True),
                "algum_celular": lembretes.email_configurado(cfg) or lembretes.telegram_configurado(cfg)
                                 or lembretes.whatsapp_configurado(cfg)}

    @app.put("/api/lembretes/config")
    def api_cfg_salvar(dados: dict = Body(default={})):
        cfg = lembretes.carregar_config()
        usuario = str(dados.get("email_usuario") or "").strip()
        if usuario:
            atual = cfg.get("email") or {}
            senha = str(dados.get("email_senha") or "").replace(" ", "") or atual.get("senha", "")
            para = str(dados.get("email_para") or "").strip() or usuario
            servidor, porta = lembretes.smtp_para(usuario)
            novo = {"usuario": usuario, "senha": senha, "para": para, "servidor": servidor, "porta": porta}
            if senha:
                try:
                    lembretes.testar_email({"email": novo})
                except Exception as e:  # noqa: BLE001
                    raise HTTPException(400, str(e)) from e
            cfg["email"] = novo
        token = str(dados.get("telegram_token") or "").strip()
        if token:
            try:
                chat, nome = lembretes.telegram_detectar_chat(token)
            except Exception as e:  # noqa: BLE001
                raise HTTPException(400, str(e)) from e
            cfg["telegram"] = {"token": token, "chat_id": chat, "nome": nome}
        if "windows" in dados:
            cfg["windows"] = bool(dados["windows"])
        lembretes.salvar_config(cfg)
        return api_cfg()

    @app.post("/api/lembretes/teste")
    def api_teste():
        canais = lembretes.enviar("Teste de lembrete — Veículos em leilão",
                                  "Se você recebeu isto, os lembretes estão funcionando. 🚗")
        if not canais:
            raise HTTPException(400, "nenhum canal funcionou — confira o e-mail e a senha de app")
        return {"canais": canais}

    def vigia():  # enquanto o site estiver aberto, confere os lembretes a cada 5 min
        while True:
            try:
                lembretes.verificar(caminho_banco)
            except Exception as e:  # noqa: BLE001
                log.warning("verificação de lembretes falhou: %s", e)
            time.sleep(300)

    threading.Thread(target=vigia, daemon=True, name="lembretes").start()

    @app.get("/api/status")
    def api_status():
        d = banco.status(con)
        try:
            d["link_publico"] = (RAIZ / "link_publico.txt").read_text(encoding="utf-8").strip()
        except OSError:
            d["link_publico"] = ""
        return d

    @app.get("/api/csv")
    def api_csv(request: Request):
        res = banco.buscar(con, filtros(request), 1, 500)
        total, itens = res["total"], res["itens"]
        pag = 2
        while len(itens) < min(total, 50000):
            mais = banco.buscar(con, filtros(request), pag, 500)["itens"]
            if not mais:
                break
            itens += mais
            pag += 1
        buf = io.StringIO()
        buf.write("﻿")  # BOM: acentos corretos no Excel
        w = csv.writer(buf, delimiter=";")
        cols = ["titulo", "marca", "modelo", "ano_fab", "ano_modelo", "km", "condicao", "tipo", "origem", "uf", "cidade",
                "preco_ref", "valor_avaliacao", "lance_atual", "data_encerramento", "status", "fonte", "link"]
        w.writerow(cols)
        for r in itens:
            w.writerow([str(r.get(c) if r.get(c) is not None else "").replace(".", ",") if c in ("preco_ref", "valor_avaliacao", "lance_atual")
                        else r.get(c) if r.get(c) is not None else "" for c in cols])
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                                 headers={"Content-Disposition": 'attachment; filename="veiculos_leilao.csv"'})

    @app.get("/")
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=WEB), name="static")
    return app
