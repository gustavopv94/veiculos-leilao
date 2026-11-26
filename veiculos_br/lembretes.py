"""Lembretes de leilão dos favoritos: Telegram, notificação do Windows e (opcional) WhatsApp via CallMeBot.

Configuração em `lembretes.json` (na pasta leiloes):
    {"telegram": {"token": "123:ABC...", "chat_id": "987654"}, "windows": true}

Telegram:
  1. No Telegram, abra o @BotFather, envie /newbot e siga os passos. Ele devolve um token.
  2. Abra o seu bot novo e envie qualquer mensagem (ex.: /start).
  3. Informe o token (no 🔔 do site ou com --telegram-token). O chat é detectado sozinho.
"""
from __future__ import annotations

import html
import json
import logging
import os
import smtplib
import ssl
import subprocess
import sys
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

import requests

from . import RAIZ, banco

log = logging.getLogger("veiculos_br")
CONFIG = RAIZ / "lembretes.json"
TAREFA = "VeiculosLeilao-Lembretes"


def carregar_config() -> dict:
    if os.environ.get("EMAIL_USUARIO"):  # GitHub Actions: configuração pelos "secrets" do repositório
        u = os.environ["EMAIL_USUARIO"].strip()
        servidor, porta = smtp_para(u)
        return {"email": {"usuario": u, "senha": os.environ.get("EMAIL_SENHA", "").replace(" ", ""),
                          "para": (os.environ.get("EMAIL_PARA") or u).strip(), "servidor": servidor, "porta": porta},
                "windows": False}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"windows": True}


def salvar_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


SMTP_PADRAO = {"gmail.com": ("smtp.gmail.com", 587), "googlemail.com": ("smtp.gmail.com", 587),
               "outlook.com": ("smtp-mail.outlook.com", 587), "hotmail.com": ("smtp-mail.outlook.com", 587),
               "live.com": ("smtp-mail.outlook.com", 587), "yahoo.com": ("smtp.mail.yahoo.com", 587),
               "yahoo.com.br": ("smtp.mail.yahoo.com", 587), "icloud.com": ("smtp.mail.me.com", 587),
               "uol.com.br": ("smtps.uol.com.br", 587), "bol.com.br": ("smtps.bol.com.br", 587)}


def smtp_para(usuario: str) -> tuple[str, int]:
    dominio = usuario.rpartition("@")[2].lower()
    return SMTP_PADRAO.get(dominio, (f"smtp.{dominio}", 587))


def email_configurado(cfg: dict | None = None) -> bool:
    e = (cfg or carregar_config()).get("email") or {}
    return bool(e.get("usuario") and e.get("senha") and e.get("para"))


def enviar_email(titulo: str, texto: str, cfg: dict) -> None:
    e = cfg["email"]
    servidor, porta = e.get("servidor") or smtp_para(e["usuario"])[0], int(e.get("porta") or smtp_para(e["usuario"])[1])
    msg = EmailMessage()
    msg["Subject"] = titulo
    msg["From"] = e["usuario"]
    msg["To"] = e["para"]
    msg.set_content(texto)
    linhas = []
    for ln in texto.splitlines():
        lnk = html.escape(ln)
        linhas.append(f'<a href="{lnk}">{lnk}</a>' if ln.startswith("http") else lnk)
    msg.add_alternative(f"<div style='font:15px/1.5 sans-serif'><h3 style='margin:0 0 8px'>{html.escape(titulo)}</h3>"
                        + "<br>".join(linhas) + "</div>", subtype="html")
    ctx = ssl.create_default_context()
    if porta == 465:
        with smtplib.SMTP_SSL(servidor, porta, context=ctx, timeout=30) as s:
            s.login(e["usuario"], e["senha"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(servidor, porta, timeout=30) as s:
            s.starttls(context=ctx)
            s.login(e["usuario"], e["senha"])
            s.send_message(msg)


def testar_email(cfg: dict) -> None:
    """Envia o teste e traduz os erros mais comuns."""
    try:
        enviar_email("Teste de lembrete — Veículos em leilão",
                     "Se você recebeu isto, os lembretes por e-mail estão funcionando.", cfg)
    except smtplib.SMTPAuthenticationError as ex:
        raise RuntimeError("o servidor recusou usuário/senha. Use uma SENHA DE APP (não a senha normal) — "
                           f"detalhe: {ex.smtp_error.decode(errors='replace')[:160]}") from ex
    except smtplib.SMTPServerDisconnected as ex:
        raise RuntimeError("o servidor encerrou a conexão no login — quase sempre é e-mail ou senha de app errados "
                           "(confira os 16 caracteres). Se estiverem certos, um antivírus pode estar bloqueando "
                           "o envio de e-mail por programas.") from ex
    except (OSError, smtplib.SMTPException) as ex:
        raise RuntimeError(f"falha ao enviar: {ex}") from ex


def telegram_configurado(cfg: dict | None = None) -> bool:
    t = (cfg or carregar_config()).get("telegram") or {}
    return bool(t.get("token") and t.get("chat_id"))


def telegram_detectar_chat(token: str) -> tuple[str, str]:
    """Descobre o chat a partir da última mensagem enviada ao bot. Devolve (chat_id, nome)."""
    token = token.strip()
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30)
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not j.get("ok"):
        raise RuntimeError("token inválido — confira o token enviado pelo @BotFather")
    for u in reversed(j.get("result") or []):
        msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            nome = chat.get("first_name") or chat.get("title") or chat.get("username") or ""
            return str(chat["id"]), nome
    raise RuntimeError("nenhuma mensagem encontrada — abra o seu bot no Telegram, envie /start e tente de novo")


def enviar_telegram(texto: str, cfg: dict) -> None:
    t = cfg["telegram"]
    r = requests.post(f"https://api.telegram.org/bot{t['token'].strip()}/sendMessage", timeout=30,
                      json={"chat_id": t["chat_id"], "text": texto, "disable_web_page_preview": False})
    j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if not j.get("ok"):
        raise RuntimeError(f"Telegram respondeu {r.status_code}: {j.get('description') or r.text[:200]}")


def whatsapp_configurado(cfg: dict | None = None) -> bool:
    w = (cfg or carregar_config()).get("whatsapp") or {}
    return bool(w.get("telefone") and w.get("apikey"))


# --------------------------------------------------------------------------------------
# Canais
# --------------------------------------------------------------------------------------
def enviar_whatsapp(texto: str, cfg: dict) -> None:
    w = cfg["whatsapp"]
    tel = "+" + "".join(ch for ch in str(w["telefone"]) if ch.isdigit())
    r = requests.get("https://api.callmebot.com/whatsapp.php",
                     params={"phone": tel, "text": texto, "apikey": str(w["apikey"]).strip()}, timeout=30)
    corpo = r.text.lower()
    if r.status_code != 200 or "error" in corpo or "apikey is invalid" in corpo:
        raise RuntimeError(f"CallMeBot respondeu {r.status_code}: {r.text[:200]}")


_PS_TOAST = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode($env:VB_TITULO)) | Out-Null
$x.Item(1).AppendChild($t.CreateTextNode($env:VB_TEXTO)) | Out-Null
$n = [Windows.UI.Notifications.ToastNotification]::new($t)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe').Show($n)
"""


def notificar_windows(titulo: str, texto: str) -> None:
    if sys.platform != "win32":
        raise RuntimeError("notificação do Windows só funciona no Windows")
    env = dict(os.environ, VB_TITULO=titulo[:120], VB_TEXTO=texto[:400])  # textos via variável de ambiente
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_TOAST], env=env, check=True,
                   timeout=30, capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def enviar(titulo: str, texto: str, cfg: dict | None = None) -> list[str]:
    """Envia por todos os canais configurados; devolve os que funcionaram."""
    cfg = cfg or carregar_config()
    ok = []
    if email_configurado(cfg):
        try:
            enviar_email(titulo, texto, cfg)
            ok.append("email")
        except Exception as e:  # noqa: BLE001
            log.warning("E-mail falhou: %s", e)
    if telegram_configurado(cfg):
        try:
            enviar_telegram(f"🔔 {titulo}\n{texto}", cfg)
            ok.append("telegram")
        except Exception as e:  # noqa: BLE001
            log.warning("Telegram falhou: %s", e)
    if whatsapp_configurado(cfg):
        try:
            enviar_whatsapp(f"*{titulo}*\n{texto}", cfg)
            ok.append("whatsapp")
        except Exception as e:  # noqa: BLE001
            log.warning("WhatsApp falhou: %s", e)
    if cfg.get("windows", True):
        try:
            notificar_windows(titulo, texto)
            ok.append("windows")
        except Exception as e:  # noqa: BLE001
            log.warning("Notificação do Windows falhou: %s", e)
    return ok


# --------------------------------------------------------------------------------------
# Verificação
# --------------------------------------------------------------------------------------
def _faltam(data_ref: str) -> str:
    seg = (datetime.fromisoformat(data_ref) - datetime.now()).total_seconds()
    h, m = int(seg // 3600), int(seg % 3600 // 60)
    return f"{h // 24} dia(s) e {h % 24} h" if h >= 24 else f"{h} h {m:02d} min" if h else f"{m} min"


def mensagem(item: dict) -> tuple[str, str]:
    titulo = f"Leilão em {_faltam(item['data_ref'])}: {item['titulo'][:70]}"
    partes = [
        f"🗓 {item['data_encerramento'] or item['data_ref'].replace('T', ' ')}",
        f"💰 R$ {item['preco_ref']:,.0f}".replace(",", ".") if item.get("preco_ref") else "",
        f"📍 {'/'.join(v for v in (item.get('cidade'), item.get('uf')) if v)}" if item.get("uf") else "",
        f"🚗 {item['condicao']} · {item['fonte']}",
        f"📝 {item['nota']}" if item.get("nota") else "",
        item["link"],
    ]
    return titulo, "\n".join(p for p in partes if p)


def verificar(caminho_banco=banco.BANCO_PADRAO) -> int:
    con = banco.conectar(caminho_banco)
    cfg = carregar_config()
    enviados = 0
    for item in banco.lembretes_pendentes(con):
        titulo, texto = mensagem(item)
        canais = enviar(titulo, texto, cfg)
        if canais:
            banco.registrar_lembrete(con, item, ",".join(canais))
            enviados += 1
            log.info("Lembrete enviado (%s): %s", ",".join(canais), item["titulo"])
    con.close()
    return enviados


def _rotulo_ant(m: int) -> str:
    return f"{m} min" if m < 60 else f"{m // 60} h" if m < 1440 else f"{m // 1440} dia{'s' if m >= 2880 else ''}"


def _data_ref_do_favorito(f: dict) -> str:
    """Data ISO salva no favorito (data_ref) ou convertida de data_encerramento 'dd/mm/aaaa hh:mm'."""
    if f.get("data_ref"):
        return f["data_ref"]
    try:
        return datetime.strptime((f.get("data_encerramento") or "").strip()[:16], "%d/%m/%Y %H:%M").isoformat()
    except ValueError:
        return ""


def verificar_estatico(lista_json: Path, favoritos_json: Path, enviados_json: Path) -> int:
    """Versão GitHub Actions: lotes vêm do lista.json publicado e os favoritos do favoritos.json do repositório.

    Manda um e-mail de confirmação quando um lembrete novo aparece e o aviso quando chega a hora
    (na primeira execução depois de `leilão - antecedência`, até o horário do leilão)."""
    from datetime import timedelta
    try:
        favs = json.loads(Path(favoritos_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        favs = {}
    if not any(f.get("lembrar") for f in favs.values()):
        print("Nenhum favorito com lembrete.")
        return 0
    lst = json.loads(Path(lista_json).read_text(encoding="utf-8"))
    lotes = {r[0]: dict(zip(lst["cols"], r)) for r in lst["rows"]}
    try:
        enviados = json.loads(Path(enviados_json).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        enviados = {}
    cfg, agora, n = carregar_config(), datetime.now(), 0
    if not email_configurado(cfg):
        print("AVISO: e-mail não configurado (secrets EMAIL_USUARIO / EMAIL_SENHA / EMAIL_PARA).")
    for id_, f in favs.items():
        if not f.get("lembrar"):
            continue
        l = lotes.get(id_) or {}
        item = {"titulo": f.get("titulo") or id_, "link": f.get("link", ""), "preco_ref": f.get("preco_ref"),
                "cidade": f.get("cidade"), "uf": f.get("uf"), "condicao": f.get("condicao") or "Não informado",
                "fonte": f.get("fonte", ""), "data_encerramento": f.get("data_encerramento", ""),
                "data_ref": _data_ref_do_favorito(f)}
        item.update({k: v for k, v in l.items() if v not in (None, "")})  # dados da última coleta têm prioridade
        item["nota"] = f.get("nota", "")
        ant = int(f.get("antecedencia_min") or 60)
        if not item.get("data_ref"):
            print(f"- {item['titulo'][:60]}: sem data de leilão ainda")
            continue
        quando = datetime.fromisoformat(item["data_ref"])
        if quando <= agora:
            print(f"- {item['titulo'][:60]}: leilão já passou ({item['data_ref']})")
            continue
        chave = f"{id_}|{item['data_ref']}|{ant}"
        if quando - timedelta(minutes=ant) <= agora:
            if chave in enviados:
                print(f"- {item['titulo'][:60]}: aviso já enviado")
                continue
            titulo, texto = mensagem(item)
            if enviar(titulo, texto, cfg):
                enviados[chave] = agora.isoformat(timespec="seconds")
                n += 1
                print(f"- {item['titulo'][:60]}: AVISO ENVIADO")
            else:
                print(f"- {item['titulo'][:60]}: falha ao enviar o aviso")
        elif chave + "|conf" not in enviados:
            titulo = f"✔ Lembrete marcado: {item['titulo'][:70]}"
            texto = (f"Você vai receber um aviso {_rotulo_ant(ant)} antes do leilão "
                     "(o GitHub pode atrasar alguns minutos).\n\n" + mensagem(item)[1])
            if enviar(titulo, texto, cfg):
                enviados[chave + "|conf"] = agora.isoformat(timespec="seconds")
                print(f"- {item['titulo'][:60]}: confirmação enviada (aviso em {quando - timedelta(minutes=ant)})")
        else:
            print(f"- {item['titulo'][:60]}: aguardando (aviso em {quando - timedelta(minutes=ant)})")
    limite = (agora - timedelta(days=60)).isoformat()
    enviados = {k: v for k, v in enviados.items() if v >= limite}
    Path(enviados_json).write_text(json.dumps(enviados, indent=1), encoding="utf-8")
    return n


def enviar_teste_estatico() -> None:
    cfg = carregar_config()
    if not email_configurado(cfg):
        raise SystemExit("E-mail não configurado: crie os secrets EMAIL_USUARIO, EMAIL_SENHA e EMAIL_PARA no repositório.")
    testar_email(cfg)
    print(f"E-mail de teste enviado para {cfg['email']['para']}.")


# --------------------------------------------------------------------------------------
# Tarefa agendada do Windows (confere a cada 10 min, mesmo com o site fechado)
# --------------------------------------------------------------------------------------
def agendar(intervalo_min: int = 10) -> str:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    interp = pyw if pyw.exists() else exe
    script = RAIZ / "veiculos_br" / "rodar_lembretes.pyw"
    cmd = ["schtasks", "/Create", "/F", "/SC", "MINUTE", "/MO", str(intervalo_min), "/TN", TAREFA,
           "/TR", f'"{interp}" "{script}"']
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)
    return f"Tarefa '{TAREFA}' criada: confere os lembretes a cada {intervalo_min} min."


def desagendar() -> str:
    r = subprocess.run(["schtasks", "/Delete", "/F", "/TN", TAREFA], capture_output=True, text=True)
    return "Tarefa removida." if r.returncode == 0 else (r.stderr or r.stdout).strip()
