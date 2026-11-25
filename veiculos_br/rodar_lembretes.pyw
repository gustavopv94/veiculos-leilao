"""Executado pela tarefa agendada do Windows (sem janela): envia os lembretes pendentes."""
import logging
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
logging.basicConfig(filename=str(RAIZ / "lembretes.log"), level=logging.INFO, format="%(asctime)s %(message)s")

from veiculos_br.lembretes import verificar  # noqa: E402

try:
    verificar()
except Exception:  # noqa: BLE001
    logging.exception("falha ao verificar lembretes")
