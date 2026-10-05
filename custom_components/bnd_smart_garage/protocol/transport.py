"""TLS setup for the hub's self-signed, legacy-only TLS endpoint."""

from __future__ import annotations

import asyncio
import socket
import ssl

from cryptography import x509

from .const import CONTROL_PORT
from .errors import HubUnreachableError


def hub_ssl_context() -> ssl.SSLContext:
    """Build the permissive SSL context needed to handshake with the hub at all.

    The hub presents a self-signed certificate and only negotiates old TLS
    versions with weak ciphers, so verification is off and the security level
    is dropped. Traffic is still protected by the protocol's own AES layer.
    """
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        context.minimum_version = ssl.TLSVersion.TLSv1
    except (AttributeError, ValueError):
        pass
    try:
        context.set_ciphers("DEFAULT:@SECLEVEL=0")
    except ssl.SSLError:
        pass
    return context


def _read_hub_id_blocking(host: str, port: int, timeout: float) -> str:
    context = hub_ssl_context()
    try:
        with (
            socket.create_connection((host, port), timeout=timeout) as raw,
            context.wrap_socket(raw) as tls,
        ):
            cert_der = tls.getpeercert(binary_form=True)
    except OSError as err:
        raise HubUnreachableError(f"could not connect to {host}:{port}: {err}") from err
    if not cert_der:
        raise HubUnreachableError(f"hub at {host}:{port} presented no certificate")
    names = x509.load_der_x509_certificate(cert_der).subject.get_attributes_for_oid(
        x509.NameOID.COMMON_NAME
    )
    if not names:
        raise HubUnreachableError("hub certificate has no Common Name (hub ID)")
    return str(names[0].value)


async def read_hub_id(host: str, port: int = CONTROL_PORT, timeout: float = 10) -> str:
    """Return the hub's ID, which it publishes as its certificate's Common Name.

    Runs in a worker thread: with verification disabled, asyncio's TLS layer
    won't hand back the peer certificate, so this uses blocking sockets.
    """
    return await asyncio.to_thread(_read_hub_id_blocking, host, port, timeout)
