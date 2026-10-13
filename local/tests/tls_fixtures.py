"""Ephemeral certificates for local TLS tests; never install trust or run services."""

import ipaddress
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def _write_fixture(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'wb') as handle:
        handle.write(content)


class TemporaryCertificateAuthority:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.ca_file = self.directory / 'fixture-ca.pem'
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Disposable Buddy test CA')])
        now = datetime.now(timezone.utc)
        self.certificate = (
            x509.CertificateBuilder()
            .subject_name(name).issuer_name(name).public_key(self._key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=3))
            .not_valid_after(now + timedelta(days=3))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True,
                    crl_sign=True, encipher_only=False, decipher_only=False,
                ), critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self._key.public_key()), critical=False)
            .sign(self._key, hashes.SHA256())
        )
        _write_fixture(self.ca_file, self.certificate.public_bytes(serialization.Encoding.PEM))

    def issue_server(
        self, name: str, *, dns_names=('localhost',),
        ip_addresses=('127.0.0.1', '::1'), expired=False,
        not_yet_valid=False, valid_days=1, self_signed=False,
    ) -> tuple[Path, Path]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        now = datetime.now(timezone.utc)
        starts = now - timedelta(days=2)
        ends = now + timedelta(days=valid_days)
        if expired:
            ends = now - timedelta(hours=1)
        elif not_yet_valid:
            starts = now + timedelta(days=1)
            ends = starts + timedelta(days=valid_days)
        issuer = self.certificate.subject
        signer = self._key
        if self_signed:
            issuer = subject
            signer = key
        names = [x509.DNSName(value) for value in dns_names]
        names.extend(x509.IPAddress(ipaddress.ip_address(value)) for value in ip_addresses)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject).issuer_name(issuer).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(starts)
            .not_valid_after(ends)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectAlternativeName(names), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=True,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False,
                    crl_sign=False, encipher_only=False, decipher_only=False,
                ), critical=True,
            )
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(signer.public_key()), critical=False)
            .sign(signer, hashes.SHA256())
        )
        cert_file = self.directory / f'{name}-certificate.pem'
        key_file = self.directory / f'{name}-private-key.pem'
        _write_fixture(cert_file, certificate.public_bytes(serialization.Encoding.PEM))
        _write_fixture(
            key_file,
            key.private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        return cert_file, key_file

    def issue_self_signed_server(self, name: str, **certificate_options) -> tuple[Path, Path]:
        return self.issue_server(name, self_signed=True, **certificate_options)
