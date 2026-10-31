import datetime
import ipaddress
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import requests
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from woob.browser import Browser


@pytest.fixture(scope="session")
def tls_material(tmp_path_factory):
    """Self-signed TLS certificate and key for localhost."""
    directory = tmp_path_factory.mktemp("tls")
    cert_file = directory / "cert.pem"
    key_file = directory / "key.pem"

    key = ec.generate_private_key(ec.SECP256R1())
    key_file.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )

    now = datetime.datetime.now(datetime.UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ]
    )

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_file.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    return cert_file, key_file


@pytest.fixture
def https_server(tls_material: tuple[Path, Path]):
    """Webserver with TLS certificate."""
    cert_file, key_file = tls_material

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = b"OK"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass  # No need to log

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    server.socket = context.wrap_socket(server.socket, server_side=True)

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        yield (
            f"https://localhost:{server.server_port}",
            cert_file,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class NoRetryBrowser(Browser):
    MAX_RETRIES = 0  # don't want to retry when we got a SSL error


class TestBrowser:
    def test_verify_true(self, https_server):
        """Browser verifies TLS certificate unless explicitely requested not to."""
        url, bundle_path = https_server

        # Browser with static VERIFY attribute
        class BrowserVerifyTrue(NoRetryBrowser):
            VERIFY = True

        with pytest.raises(requests.exceptions.SSLError):
            BrowserVerifyTrue().open(url)

        r = BrowserVerifyTrue(verify=False).open(url)
        assert r.status_code == 200

        r = BrowserVerifyTrue().open(url, verify=False)
        assert r.status_code == 200

        r = BrowserVerifyTrue().open(url, verify=bundle_path)
        assert r.status_code == 200

    def test_verify_false(self, https_server):
        """Browser does not verify TLS certificate unless explicitly requested."""
        url, _ = https_server

        class BrowserVerifyFalse(NoRetryBrowser):
            VERIFY = False

        with pytest.raises(requests.exceptions.SSLError):
            BrowserVerifyFalse().open(url, verify=True)

        r = BrowserVerifyFalse().open(url)
        assert r.status_code == 200

    def test_verify_bundle(self, https_server):
        """Browser trusts VERIFY provided bundle."""
        url, bundle_path = https_server

        class BrowserVerifyPath(NoRetryBrowser):
            VERIFY = bundle_path

        r = BrowserVerifyPath().open(url)
        assert r.status_code == 200
