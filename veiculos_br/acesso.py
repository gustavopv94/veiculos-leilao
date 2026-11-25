"""Senha de acesso para quem entra pela internet (o acesso pelo próprio PC continua livre)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets

from . import RAIZ

ARQ = RAIZ / "acesso.json"
_LOCAIS = {"127.0.0.1", "::1", "localhost"}


def _hash(senha: str, sal: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), sal, 200_000).hex()


def definir_senha(senha: str, usuario: str = "leilao") -> None:
    sal = os.urandom(16)
    ARQ.write_text(json.dumps({"usuario": usuario, "sal": sal.hex(), "hash": _hash(senha, sal)}), encoding="utf-8")


def carregar() -> dict | None:
    try:
        return json.loads(ARQ.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def conferir(usuario: str, senha: str, cfg: dict) -> bool:
    ok_u = secrets.compare_digest(usuario.encode(), cfg["usuario"].encode())
    ok_s = hmac.compare_digest(_hash(senha, bytes.fromhex(cfg["sal"])), cfg["hash"])
    return ok_u and ok_s


def pedido_externo(headers, ip: str) -> bool:
    """Veio pela internet? (o túnel do Cloudflare entrega pelo localhost, mas marca o cabeçalho cf-connecting-ip)"""
    return bool(headers.get("cf-connecting-ip") or headers.get("x-forwarded-for")) or ip not in _LOCAIS


def autorizado(auth_header: str | None) -> bool:
    cfg = carregar()
    if not cfg:
        return False  # sem senha definida, ninguém de fora entra
    if not auth_header or not auth_header.lower().startswith("basic "):
        return False
    try:
        usuario, _, senha = base64.b64decode(auth_header[6:]).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001
        return False
    return conferir(usuario, senha, cfg)
