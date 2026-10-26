"""
Web Push (notificações no celular) sem dependências externas além de `cryptography`.

Implementa:
  - VAPID (RFC 8292): JWT ES256 assinado com a chave do servidor
  - Criptografia do conteúdo aes128gcm (RFC 8188 + RFC 8291)

As chaves VAPID são geradas sozinhas na primeira vez e guardadas no banco
(tabela app_config). Para fixá-las por variável de ambiente, use
VAPID_PRIVATE_KEY (PEM) e VAPID_PUBLIC_KEY (base64url).
"""
import base64
import json
import os
import struct
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64u_dec(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _pub_bytes(pub) -> bytes:
    return pub.public_bytes(serialization.Encoding.X962,
                            serialization.PublicFormat.UncompressedPoint)


# ─────────────────────────────────────────────────────────────
#  Chaves VAPID
# ─────────────────────────────────────────────────────────────
_cache = {}


def chaves_vapid():
    """Retorna (chave_privada, chave_publica_b64url). Gera e persiste se não existir."""
    if "k" in _cache:
        return _cache["k"]
    pem = os.environ.get("VAPID_PRIVATE_KEY")
    if not pem:
        from app import db
        from app.models import AppConfig
        from sqlalchemy.exc import IntegrityError
        row = AppConfig.query.get("vapid_private_pem")
        if row is None:
            novo = ec.generate_private_key(ec.SECP256R1()).private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption()).decode()
            try:
                db.session.add(AppConfig(key="vapid_private_pem", value=novo))
                db.session.commit()
            except IntegrityError:          # outro worker gerou ao mesmo tempo
                db.session.rollback()
            row = AppConfig.query.get("vapid_private_pem")
        pem = row.value
    priv = serialization.load_pem_private_key(pem.replace("\\n", "\n").encode(), None)
    pub = _b64u(_pub_bytes(priv.public_key()))
    _cache["k"] = (priv, pub)
    return _cache["k"]


def _jwt_vapid(endpoint: str, priv, sub: str) -> str:
    u = urlparse(endpoint)
    header = _b64u(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
    claims = _b64u(json.dumps({
        "aud": f"{u.scheme}://{u.netloc}",
        "exp": int(time.time()) + 12 * 3600,
        "sub": sub,
    }).encode())
    msg = f"{header}.{claims}".encode()
    r, s = decode_dss_signature(priv.sign(msg, ec.ECDSA(hashes.SHA256())))
    return f"{header}.{claims}.{_b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


# ─────────────────────────────────────────────────────────────
#  Criptografia aes128gcm (RFC 8291)
# ─────────────────────────────────────────────────────────────
def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def criptografar(payload: bytes, p256dh_b64: str, auth_b64: str) -> bytes:
    ua_pub = _b64u_dec(p256dh_b64)
    auth = _b64u_dec(auth_b64)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_pub)

    as_priv = ec.generate_private_key(ec.SECP256R1())
    as_pub = _pub_bytes(as_priv.public_key())
    shared = as_priv.exchange(ec.ECDH(), ua_key)

    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + ua_pub + as_pub, 32)
    salt = os.urandom(16)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    rs = 4096
    cifrado = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # registro único
    return salt + struct.pack("!IB", rs, len(as_pub)) + as_pub + cifrado


# ─────────────────────────────────────────────────────────────
#  Envio
# ─────────────────────────────────────────────────────────────
class InscricaoExpirada(Exception):
    """O navegador cancelou a inscrição (404/410) — deve ser apagada."""


def enviar(inscricao: dict, dados: dict, sub: str = "mailto:avisos@nossodindin.app",
           ttl: int = 24 * 3600, urgencia: str = "normal") -> int:
    """
    inscricao = {"endpoint": ..., "keys": {"p256dh": ..., "auth": ...}}
    dados     = dict serializado em JSON e entregue ao service worker
    """
    priv, pub = chaves_vapid()
    endpoint = inscricao["endpoint"]
    corpo = criptografar(json.dumps(dados, ensure_ascii=False).encode("utf-8"),
                         inscricao["keys"]["p256dh"], inscricao["keys"]["auth"])
    req = urllib.request.Request(endpoint, data=corpo, method="POST", headers={
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(ttl),
        "Urgency": urgencia,
        "Authorization": f"vapid t={_jwt_vapid(endpoint, priv, sub)}, k={pub}",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        if e.code in (404, 410):
            raise InscricaoExpirada(endpoint)
        raise RuntimeError(f"push {e.code}: {e.read()[:200]!r}")


def enviar_para_usuario(user_id: int, dados: dict) -> int:
    """Envia para todos os aparelhos do usuário. Retorna quantos receberam."""
    from app import db
    from app.models import PushInscricao, User
    u = User.query.get(user_id)
    sub = f"mailto:{os.environ.get('VAPID_EMAIL') or (u.email if u else 'avisos@nossodindin.app')}"
    ok = 0
    for insc in PushInscricao.query.filter_by(user_id=user_id).all():
        try:
            enviar({"endpoint": insc.endpoint,
                    "keys": {"p256dh": insc.p256dh, "auth": insc.auth}}, dados, sub=sub)
            ok += 1
        except InscricaoExpirada:
            db.session.delete(insc)
            db.session.commit()
        except Exception as e:
            print(f"[push] falha usuário {user_id}: {e}")
    return ok
