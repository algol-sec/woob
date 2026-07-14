import itertools
import ssl
import sys

import pytest
import requests

from woob.browser import Browser


# Both hosts serve a self-signed cert; under CI load either one can
# occasionally refuse or reset the connection, so alternate between them
# instead of letting the whole test flake out on a single provider's hiccup.
BADSSL_HOSTS = ("self-signed.badssl.com", "self-signed.testserver.host")
BADSSL_MAX_ATTEMPTS = 6


def _get_server_certificate(host):
    if sys.version_info >= (3, 10):
        return ssl.get_server_certificate((host, 443))

    # On older versions of Python, ssl.get_server_certificate() doesn't
    # support SNI.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS)
    with ssl.create_connection((host, 443)) as sock:
        with context.wrap_socket(sock, server_hostname=host) as sslsock:
            der_cert = sslsock.getpeercert(True)
            return ssl.DER_cert_to_PEM_cert(der_cert)


@pytest.fixture(scope="function")
def badssl_self_signed(tmp_path_factory):
    bundle_path = tmp_path_factory.mktemp("ca_bundle") / "selfsignedbundle.pem"

    # Try each host in turn, cycling back to the first if needed, and settle
    # on whichever one actually answers.
    last_exc = None
    for host in itertools.islice(itertools.cycle(BADSSL_HOSTS), BADSSL_MAX_ATTEMPTS):
        try:
            pem_cert = _get_server_certificate(host)
        except OSError as exc:
            last_exc = exc
            continue

        with open(bundle_path, "w", encoding="utf-8") as fp:
            fp.write(pem_cert)

        return host, bundle_path

    raise last_exc


class TestBrowser:
    def test_verify(self, badssl_self_signed):
        host, bundle_path = badssl_self_signed
        url = f"https://{host}/"

        class NoRetryBrowser(Browser):
            MAX_RETRIES = 0  # don't want to retry when we got a SSL error

        # Browser with static VERIFY attribute
        class BrowserVerifyTrue(NoRetryBrowser):
            VERIFY = True

        class BrowserVerifyFalse(NoRetryBrowser):
            VERIFY = False

        class BrowserVerifyPath(NoRetryBrowser):
            VERIFY = bundle_path

        # Tests which fail
        with pytest.raises(requests.exceptions.SSLError):
            BrowserVerifyTrue().open(url)

        with pytest.raises(requests.exceptions.SSLError):
            BrowserVerifyFalse().open(url, verify=True)

        # Tests which succeed
        r = BrowserVerifyTrue(verify=False).open(url)
        assert r.status_code == 200

        r = BrowserVerifyTrue().open(url, verify=False)
        assert r.status_code == 200

        r = BrowserVerifyTrue().open(url, verify=bundle_path)
        assert r.status_code == 200

        r = BrowserVerifyFalse().open(url)
        assert r.status_code == 200

        r = BrowserVerifyPath().open(url)
        assert r.status_code == 200
