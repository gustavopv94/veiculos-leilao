"""Executado ao entrar no Windows (sem janela): sobe o site local + link público (Cloudflare Tunnel)."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
log_arq = open(RAIZ / "site.log", "a", encoding="utf-8")  # noqa: SIM115
sys.stdout = sys.stderr = log_arq

from veiculos_br.__main__ import main  # noqa: E402

main(["publicar"])
