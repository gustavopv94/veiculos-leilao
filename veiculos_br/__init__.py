"""Veículos em leilão no Brasil: coleta (Copart, VIP, Sodré e as fontes do leiloes_mg) + site local de busca."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:  # permite "import leiloes_mg" de qualquer pasta
    sys.path.insert(0, str(RAIZ))
