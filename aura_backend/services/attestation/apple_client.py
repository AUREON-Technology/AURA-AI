"""Apple App Attest trust anchors.

Production loads Apple's published App Attest Root CA. Tests inject a
``AppleAppAttestClient`` that returns a fixture CA so verification is
exercised without network or Apple's real roots.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Apple App Attestation Root CA (ECC), published at
# https://www.apple.com/certificateauthority/Apple_App_Attestation_Root_CA.pem
APPLE_APP_ATTEST_ROOT_CA_PEM = """-----BEGIN CERTIFICATE-----
MIICITCCAaegAwIBAgIQC/O+DvHN0uD7jG5yH2IXmDAKBggqhkjOPQQDAzBSMSYw
JAYDVQQDDB1BcHBsZSBBcHAgQXR0ZXN0YXRpb24gUm9vdCBDQTETMBEGA1UECgwK
QXBwbGUgSW5jLjETMBEGA1UECAwKQ2FsaWZvcm5pYTAeFw0yMDAzMTgxODMyNTNa
Fw00NTAzMTUwMDAwMDBaMFIxJjAkBgNVBAMMHUFwcGxlIEFwcCBBdHRlc3RhdGlv
biBSb290IENBMRMwEQYDVQQKDApBcHBsZSBJbmMuMRMwEQYDVQQIDApDYWxpZm9y
bmlhMHYwEAYHKoZIzj0CAQYFK4EEACIDYgAERTHhmLW07ATaFQIEVwTtT4dyctdh
NbJhFs/Ii2FdCgAHGbpphY3+d8qjuDngIN3WVhQUBHAoMeQ/cLiP1sOUtgjqK9au
Yen1mMEvRq9Sk3Jm5X8U62H+xTD3FE9TgS41o0IwQDAPBgNVHRMBAf8EBTADAQH/
MB0GA1UdDgQWBBSskRBTM72+aEH/pwyp5frq5eWKoTAOBgNVHQ8BAf8EBAMCAQYw
CgYIKoZIzj0EAwMDaAAwZQIwQgFGnByvsiVbpTKwSga0kP0e8EeDS4+sQmTvb7vn
53O5+FRXgeLhpJ06ysC5PrOyAjEAp5U4xDgEgllF7En3VcE3iexZZtKeYnpqtijV
oyFraWVIyd/dganmrduC1bmTBGwD
-----END CERTIFICATE-----
"""


@runtime_checkable
class AppleAppAttestClient(Protocol):
    """Supplies App Attest trust anchors. Mocked in unit tests."""

    def load_app_attest_roots(self) -> list[bytes]:
        """Return PEM or DER encodings of trusted App Attest root CAs."""


class ProductionAppleAppAttestClient:
    def load_app_attest_roots(self) -> list[bytes]:
        return [APPLE_APP_ATTEST_ROOT_CA_PEM.encode("ascii")]


def default_apple_client() -> AppleAppAttestClient:
    return ProductionAppleAppAttestClient()
