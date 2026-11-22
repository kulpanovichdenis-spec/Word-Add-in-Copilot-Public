"""Сертификат для https://localhost (ТЗ 5.2, 10.1 п. 6).

Выпускается пара «локальный УЦ + сертификат localhost». В доверенные корневые
кладётся только УЦ, причём:
- у УЦ есть NameConstraints: он годен лишь для localhost и 127.0.0.1;
- закрытый ключ УЦ не сохраняется — после подписи он уничтожается.
Поэтому доверенный корень нельзя использовать, чтобы выпустить сертификат для
любого другого сайта, даже если украсть файлы с машины.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
from dataclasses import dataclass
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

VALIDITY_DAYS = 5 * 365
CA_NAME = "Word-Add-in-Copilot-Public Localhost CA"


@dataclass
class TlsFiles:
    ca_cert: Path  # DER, для импорта в Cert:\...\Root
    cert: Path  # PEM, сертификат localhost
    key: Path  # PEM, закрытый ключ localhost (для хоста)

    def exist(self) -> bool:
        return self.cert.is_file() and self.key.is_file()


def tls_files(folder: Path) -> TlsFiles:
    return TlsFiles(folder / "ca.cer", folder / "localhost.pem", folder / "localhost-key.pem")


def generate(folder: Path, *, days: int = VALIDITY_DAYS) -> TlsFiles:
    folder.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
    until = now + dt.timedelta(days=days)
    loopback = ipaddress.ip_address("127.0.0.1")

    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_NAME)])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True
        )
        .add_extension(
            x509.NameConstraints(
                permitted_subtrees=[x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_network("127.0.0.1/32"))],
                excluded_subtrees=None,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(loopback)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(True, False, True, False, False, False, False, False, False), critical=True
        )
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    del ca_key  # ключ УЦ нигде не сохраняется

    files = tls_files(folder)
    files.ca_cert.write_bytes(ca_cert.public_bytes(serialization.Encoding.DER))
    files.cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM) + ca_cert.public_bytes(serialization.Encoding.PEM))
    files.key.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return files
