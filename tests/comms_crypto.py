"""The browser's encryption scheme (static/comms.js) re-done in Python for the tests, with the `cryptography` package:
RSA-OAEP-3072 (SHA-256, MGF1-SHA-256) wraps a random AES-256-GCM content key; the payload is JSON. Same bytes on the wire as WebCrypto
(WebCrypto's AES-GCM output = ciphertext || 16-byte tag, exactly like cryptography's AESGCM). Tests only - the team box itself
never imports this and never holds a private key."""
import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ALG = "RSA-OAEP-3072-SHA256+AES-256-GCM"
OAEP = padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None)


def b64(b):
    return base64.b64encode(b).decode()


def unb64(s):
    return base64.b64decode(s)


class Key:
    def __init__(self, priv=None):
        self.priv = priv or rsa.generate_private_key(public_exponent=65537, key_size=3072)
        self.spki_der = self.priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self.spki = b64(self.spki_der)
        self.kid = hashlib.sha256(self.spki_der).hexdigest()[:32]

    def pem(self):
        return self.priv.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def wrap(ck, spki_b64):
    pub = serialization.load_der_public_key(unb64(spki_b64))
    return pub.encrypt(ck, OAEP)


def seal(obj, recips, ck=None):
    """recips: [(to, kid, spki_b64)] -> (envelope, content key)."""
    ck = ck or AESGCM.generate_key(256)
    iv = os.urandom(12)
    ct = AESGCM(ck).encrypt(iv, json.dumps(obj).encode(), None)
    return {"v": 1, "alg": ALG, "iv": b64(iv), "ct": b64(ct),
            "keys": [{"to": to, "kid": kid, "wk": b64(wrap(ck, spki))} for to, kid, spki in recips]}, ck


def unwrap(env, key):
    k = [x for x in env["keys"] if x["kid"] == key.kid]
    if not k:
        raise KeyError("not encrypted for this key")
    return key.priv.decrypt(unb64(k[0]["wk"]), OAEP)


def unseal(env, key):
    ck = unwrap(env, key)
    return json.loads(AESGCM(ck).decrypt(unb64(env["iv"]), unb64(env["ct"]), None)), ck


def chunk(ck, data):
    iv = os.urandom(12)
    return b64(iv), b64(AESGCM(ck).encrypt(iv, data, None))


def unchunk(ck, iv_b64, data_b64):
    return AESGCM(ck).decrypt(unb64(iv_b64), unb64(data_b64), None)
