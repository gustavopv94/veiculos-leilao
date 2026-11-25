"""Site na internet pelo Cloudflare Tunnel (link gratuito *.trycloudflare.com) + tarefas agendadas do Windows."""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import RAIZ, acesso, banco, lembretes

log = logging.getLogger("veiculos_br")
LINK = RAIZ / "link_publico.txt"
TAREFA_COLETA = "VeiculosLeilao-ColetaSemanal"
TAREFA_SITE = "VeiculosLeilao-Site"
_RE_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def achar_cloudflared() -> str:
    for c in (shutil.which("cloudflared"), r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
              r"C:\Program Files\cloudflared\cloudflared.exe"):
        if c and Path(c).exists():
            return c
    raise RuntimeError("cloudflared não encontrado. Instale com: winget install --id Cloudflare.cloudflared -e --source winget")


def _avisar_link(url: str):
    LINK.write_text(url, encoding="utf-8")
    log.info("Site público em %s", url)
    cfg = lembretes.carregar_config()
    if lembretes.email_configurado(cfg):
        try:
            lembretes.enviar_email("Link do seu site de leilões",
                                   f"O site está no ar neste endereço (ele muda quando o PC reinicia):\n{url}\n\n"
                                   f"Usuário: {(acesso.carregar() or {}).get('usuario', 'leilao')} · senha: a que você definiu.", cfg)
        except Exception as e:  # noqa: BLE001
            log.warning("não consegui enviar o link por e-mail: %s", e)


def tunel(porta: int) -> subprocess.Popen:
    """Inicia o túnel e mantém em execução; reinicia sozinho se cair."""
    exe = achar_cloudflared()

    def rodar():
        while True:
            p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{porta}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                 errors="replace", creationflags=_SEM_JANELA)
            for linha in p.stdout:
                if m := _RE_URL.search(linha):
                    _avisar_link(m[0])
            p.wait()
            log.warning("túnel caiu (código %s); reiniciando em 30 s", p.returncode)
            LINK.unlink(missing_ok=True)
            time.sleep(30)

    threading.Thread(target=rodar, daemon=True, name="tunel").start()


def publicar(porta: int = 8000, caminho_banco=banco.BANCO_PADRAO):
    import uvicorn

    from .servidor import criar_app

    if not acesso.carregar():
        raise SystemExit("Defina antes uma senha de acesso:  python -m veiculos_br senha SUA_SENHA")
    LINK.unlink(missing_ok=True)
    tunel(porta)
    uvicorn.run(criar_app(caminho_banco), host="127.0.0.1", port=porta, log_level="warning")


# --------------------------------------------------------------------------------------
# Tarefas agendadas (PowerShell ScheduledTasks: permite "executar assim que possível" se o PC estava desligado)
# --------------------------------------------------------------------------------------
DIAS = {"dom": "Sunday", "seg": "Monday", "ter": "Tuesday", "qua": "Wednesday", "qui": "Thursday", "sex": "Friday",
        "sab": "Saturday"}


def _interp_sem_janela() -> str:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    return str(pyw if pyw.exists() else exe)


def _registrar(nome: str, gatilho_ps: str, script: Path, limite_h: int) -> None:
    ps = f"""
$a = New-ScheduledTaskAction -Execute '{_interp_sem_janela()}' -Argument '"{script}"' -WorkingDirectory '{RAIZ}'
$t = {gatilho_ps}
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
     -ExecutionTimeLimit (New-TimeSpan -Hours {limite_h}) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName '{nome}' -Action $a -Trigger $t -Settings $s -Force | Out-Null
"""
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip())


def agendar(dia: str = "seg", hora: str = "08:00") -> list[str]:
    dia_en = DIAS[dia]
    pasta = RAIZ / "veiculos_br"
    _registrar(TAREFA_COLETA, f"New-ScheduledTaskTrigger -Weekly -DaysOfWeek {dia_en} -At '{hora}'",
               pasta / "rodar_coleta.pyw", 3)
    _registrar(TAREFA_SITE, "New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME", pasta / "rodar_site.pyw", 9999)
    return [f"Coleta semanal: toda {dia} às {hora} (se o PC estiver desligado, roda assim que ligar).",
            "Site + link público: sobe sozinho quando você entra no Windows."]


def desagendar() -> list[str]:
    out = []
    for nome in (TAREFA_COLETA, TAREFA_SITE):
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            f"Unregister-ScheduledTask -TaskName '{nome}' -Confirm:$false"], capture_output=True, text=True)
        out.append(f"{nome}: {'removida' if r.returncode == 0 else 'não encontrada'}")
    return out
