"""
Veículos em leilão — Brasil
===========================

  python -m veiculos_br coletar                    # todas as fontes, Brasil todo
  python -m veiculos_br coletar --rapido           # sem abrir a página de cada lote (bem mais rápido)
  python -m veiculos_br coletar --fontes copart,vip,sodre
  python -m veiculos_br servir --abrir             # abre o site em http://127.0.0.1:8000

Fontes: copart, vip, sodre, mega, superbid, franco, ljmg, gp, danielgarcia, bolsa, detran, palacio
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import banco  # noqa: F401  (também ajusta o sys.path para achar leiloes_mg)
import leiloes_mg as B

from .fontes_novas import VIP, Copart, SodreBR
from .normaliza import agora_iso, para_linha, relatorio_preenchimento

log = logging.getLogger("veiculos_br")

# ordem de exibição; as do leiloes_mg entram só se tiverem veículos
FONTES = [Copart, VIP, SodreBR] + [c for c in B.FONTES if B.VEICULOS in c.categorias and c is not B.Sodre]


def coletar(args) -> int:
    B.definir_escopo(None)  # Brasil todo
    escolhidas = {x.strip().lower() for x in args.fontes.split(",") if x.strip()}
    invalidas = escolhidas - {c.chave for c in FONTES}
    if invalidas:
        sys.exit(f"fonte(s) desconhecida(s): {', '.join(sorted(invalidas))}. Opções: {', '.join(c.chave for c in FONTES)}")
    cfg = B.Config(detalhes=not args.rapido, workers=args.workers, max_detalhes=args.max_detalhes, sodre_todos=True)
    http = B.Http(intervalo=args.intervalo)
    fontes = [c(http, cfg) for c in FONTES if not escolhidas or c.chave in escolhidas]
    log.info("Coletando veículos (Brasil) em %d fontes: %s%s", len(fontes), ", ".join(f.nome for f in fontes),
             " — modo rápido" if args.rapido else "")
    t0 = time.time()
    lotes, status = [], []

    def roda(f):
        ini = time.time()
        try:
            ls = [l for l in f.coletar() if l.categoria == B.VEICULOS]
            for l in ls:
                l._chave = f.chave
            return f, ls, None, time.time() - ini
        except Exception as e:  # noqa: BLE001
            log.debug("Erro em %s", f.nome, exc_info=True)
            return f, [], e, time.time() - ini

    with ThreadPoolExecutor(max_workers=len(fontes)) as ex:
        for fut in as_completed([ex.submit(roda, f) for f in fontes]):
            f, ls, erro, seg = fut.result()
            s = {"fonte": f.nome, "chave": f.chave, "ok": erro is None, "n": len(ls), "segundos": round(seg),
                 "avisos": f.avisos}
            if erro is None:
                log.info("✔ %s: %d veículos (%.0fs)", f.nome, len(ls), seg)
                lotes += ls
            else:
                s["erro"] = str(erro)
                log.error("✘ %s falhou: %s", f.nome, erro)
            status.append(s)

    B.finalizar(lotes)
    agora = agora_iso()
    linhas = []
    for l in lotes:
        try:
            linhas.append(para_linha(l._chave, l, agora))
        except Exception as e:  # noqa: BLE001
            log.debug("normalização falhou (%s): %s", l.link, e, exc_info=True)
    con = banco.conectar(args.banco)
    banco.salvar(con, linhas, status, agora)
    ativos = con.execute("SELECT COUNT(*) FROM lotes WHERE ativo=1").fetchone()[0]

    print("\n" + "=" * 100)
    print("Preenchimento dos campos por fonte (nesta coleta)")
    print("=" * 100)
    print(relatorio_preenchimento(linhas) if linhas else "(nenhum lote)")
    print("\nFontes:")
    for s in sorted(status, key=lambda s: [c.chave for c in FONTES].index(s["chave"])):
        extra = "" if s["ok"] else f" — {s.get('erro')}"
        av = f"  ⚠ {'; '.join(s['avisos'])}" if s.get("avisos") else ""
        print(f"  [{'ok ' if s['ok'] else 'ERRO'}] {s['fonte']:<28} {s['n']:>6}{extra}{av}")
    print(f"\n{len(linhas)} veículos coletados em {time.time() - t0:.0f}s · {ativos} ativos no banco ({Path(args.banco).resolve()})")
    print("Para ver o site:  python -m veiculos_br servir --abrir")
    return 0 if any(s["ok"] for s in status) else 1


def servir(args) -> int:
    import uvicorn

    from .servidor import criar_app

    url = f"http://{args.host}:{args.porta}/"
    if args.abrir:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Site em {url}  (Ctrl+C para parar)")
    uvicorn.run(criar_app(args.banco), host=args.host, port=args.porta, log_level="warning")
    return 0


def cmd_lembretes(args) -> int:
    from . import lembretes as L

    if args.telefone or args.apikey:
        cfg = L.carregar_config()
        atual = cfg.get("whatsapp") or {}
        cfg["whatsapp"] = {"telefone": args.telefone or atual.get("telefone", ""), "apikey": args.apikey or atual.get("apikey", "")}
        L.salvar_config(cfg)
        print(f"WhatsApp configurado para {cfg['whatsapp']['telefone']} ({L.CONFIG})")
    if args.email:
        cfg = L.carregar_config()
        servidor, porta = L.smtp_para(args.email)
        cfg["email"] = {"usuario": args.email, "senha": (args.senha_app or "").replace(" ", ""),
                        "para": args.para or args.email, "servidor": servidor, "porta": porta}
        L.testar_email(cfg)
        L.salvar_config(cfg)
        print(f"E-mail configurado: {args.email} -> {cfg['email']['para']} (teste enviado)")
    if args.telegram_token:
        cfg = L.carregar_config()
        chat, nome = L.telegram_detectar_chat(args.telegram_token)
        cfg["telegram"] = {"token": args.telegram_token.strip(), "chat_id": chat, "nome": nome}
        L.salvar_config(cfg)
        print(f"Telegram configurado: chat de {nome or chat}")
    if args.agendar:
        print(L.agendar())
    if args.desagendar:
        print(L.desagendar())
    if args.teste:
        canais = L.enviar("Teste de lembrete — Veículos em leilão", "Se você recebeu isto, os lembretes estão funcionando. 🚗")
        print("Enviado por: " + (", ".join(canais) if canais else "nenhum canal (veja os avisos acima)"))
        return 0 if canais else 1
    if not (args.email or args.telefone or args.apikey or args.telegram_token or args.agendar or args.desagendar):
        n = L.verificar(args.banco)
        print(f"{n} lembrete(s) enviado(s).")
    return 0


def cmd_senha(args) -> int:
    from . import acesso
    if len(args.nova) < 6:
        sys.exit("use uma senha com pelo menos 6 caracteres")
    acesso.definir_senha(args.nova, args.usuario)
    print(f"Senha definida. Pela internet, entre com usuário '{args.usuario}' e essa senha.")
    return 0


def cmd_publicar(args) -> int:
    from .publicar import publicar
    print(f"Site local em http://127.0.0.1:{args.porta}/ — o link público chega por e-mail e fica em link_publico.txt")
    publicar(args.porta, args.banco)
    return 0


def cmd_agendar(args) -> int:
    from . import acesso, publicar as P
    if args.remover:
        print(chr(10).join(P.desagendar()))
        return 0
    if not acesso.carregar():
        sys.exit("Defina antes uma senha de acesso:  python -m veiculos_br senha SUA_SENHA")
    print(chr(10).join(P.agendar(args.dia, args.hora)))
    return 0


def cmd_exportar(args) -> int:
    from .exportar import exportar
    meta = exportar(args.banco, Path(args.saida), args.repo)
    print(f"Site estático em {Path(args.saida).resolve()} ({meta['ativos']} veículos)")
    if args.avisar:
        from . import lembretes as L
        cfg = L.carregar_config()
        if L.email_configurado(cfg):
            linhas = [f"{'✔' if f['ok'] else '✘'} {f['fonte']}: {f['n'] if f['ok'] else f['erro']}" for f in meta["fontes"]]
            url = f"https://{args.repo.split('/')[0]}.github.io/{args.repo.split('/')[1]}/" if "/" in args.repo else ""
            L.enviar_email(f"Coleta semanal: {meta['ativos']} veículos", chr(10).join(linhas + ["", url]), cfg)
    return 0


def cmd_lembretes_gh(args) -> int:
    from . import lembretes as L
    n = L.verificar_estatico(Path(args.lista), Path(args.favoritos), Path(args.enviados))
    print(f"{n} lembrete(s) enviado(s).")
    return 0


def main(argv=None) -> int:
    for fluxo in (sys.stdout, sys.stderr):
        try:
            fluxo.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m veiculos_br", description="Veículos em leilão no Brasil — coleta e site local.")
    ap.add_argument("--banco", default=str(banco.BANCO_PADRAO), help="arquivo SQLite (padrão: veiculos.db)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("coletar", help="coleta os lotes e atualiza o banco")
    c.add_argument("--fontes", default="", help="lista separada por vírgula: " + ",".join(x.chave for x in FONTES))
    c.add_argument("--rapido", action="store_true", help="não abre a página de cada lote")
    c.add_argument("--max-detalhes", type=int, default=0, help="máximo de páginas de lote por fonte (0 = todas)")
    c.add_argument("--workers", type=int, default=6)
    c.add_argument("--intervalo", type=float, default=0.15)
    s = sub.add_parser("servir", help="abre o site local de busca")
    s.add_argument("--porta", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--abrir", action="store_true", help="abrir no navegador")
    lb = sub.add_parser("lembretes", help="envia os lembretes pendentes (WhatsApp / Windows) e configura")
    lb.add_argument("--email", help="e-mail que ENVIA os lembretes (ex.: Gmail)")
    lb.add_argument("--senha-app", help="senha de app desse e-mail")
    lb.add_argument("--para", help="e-mail que RECEBE (padrão: o mesmo)")
    lb.add_argument("--telegram-token", help="token do seu bot (do @BotFather); envie /start ao bot antes")
    lb.add_argument("--telefone", help="seu WhatsApp com DDI e DDD, ex.: +5531999999999")
    lb.add_argument("--apikey", help="apikey recebida do CallMeBot")
    lb.add_argument("--teste", action="store_true", help="envia uma mensagem de teste")
    lb.add_argument("--agendar", action="store_true", help="cria tarefa do Windows que confere a cada 10 min")
    lb.add_argument("--desagendar", action="store_true", help="remove a tarefa do Windows")
    se = sub.add_parser("senha", help="define a senha de acesso pela internet")
    se.add_argument("nova")
    se.add_argument("--usuario", default="leilao")
    pu = sub.add_parser("publicar", help="sobe o site + link público gratuito (Cloudflare Tunnel)")
    pu.add_argument("--porta", type=int, default=8000)
    ag = sub.add_parser("agendar", help="coleta semanal + site ao ligar o PC (tarefas do Windows)")
    ag.add_argument("--dia", choices=["dom", "seg", "ter", "qua", "qui", "sex", "sab"], default="seg")
    ag.add_argument("--hora", default="08:00")
    ag.add_argument("--remover", action="store_true", help="remove as tarefas")
    ex = sub.add_parser("exportar", help="gera o site estático (GitHub Pages) a partir do banco")
    ex.add_argument("--saida", default="_site")
    ex.add_argument("--repo", default="", help="dono/repositorio no GitHub (para os favoritos)")
    ex.add_argument("--avisar", action="store_true", help="manda e-mail com o resumo da coleta")
    lg = sub.add_parser("lembretes-gh", help="(GitHub Actions) envia lembretes a partir do site publicado")
    lg.add_argument("--lista", required=True)
    lg.add_argument("--favoritos", default="favoritos.json")
    lg.add_argument("--enviados", default="lembretes_enviados.json")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    return {"coletar": coletar, "servir": servir, "lembretes": cmd_lembretes, "senha": cmd_senha,
            "publicar": cmd_publicar, "agendar": cmd_agendar, "exportar": cmd_exportar,
            "lembretes-gh": cmd_lembretes_gh}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
