"""Executado pela tarefa semanal do Windows (sem janela): coleta todas as fontes e atualiza o banco."""
import logging
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
log_arq = open(RAIZ / "coleta.log", "a", encoding="utf-8")  # noqa: SIM115
sys.stdout = sys.stderr = log_arq

from veiculos_br.__main__ import main  # noqa: E402

try:
    main(["coletar"])
except SystemExit:
    pass
except Exception:  # noqa: BLE001
    logging.exception("falha na coleta semanal")
