import itertools
from unittest import TestCase

import requests

from woob.browser import Browser
from woob.browser.adapters import LowSecHTTPAdapter


# Both hosts serve a certificate with a weak (1024-bit) DH key; under CI load
# either one can occasionally refuse or reset the connection, so alternate
# between them across a few rounds instead of letting the whole test flake
# out on a single host's hiccup.
DH1024_HOSTS = ("dh1024.badssl.com", "dh1024.testserver.host")
DH1024_MAX_ATTEMPTS = 6


class TestAdapter(TestCase):
    def test_ciphers(self):
        last_exc = None
        for host in itertools.islice(itertools.cycle(DH1024_HOSTS), DH1024_MAX_ATTEMPTS):
            try:
                self._check_ciphers(f"https://{host}/")
                return
            except requests.exceptions.ConnectionError as exc:
                last_exc = exc
        raise last_exc

    def _check_ciphers(self, url):
        # Test there is an exception without ciphers supplied.
        browser = Browser()
        self.assertRaises(requests.exceptions.SSLError, browser.open, url)

        # Test a browser with more permissive ciphers.
        class PermissiveBrowser(Browser):
            HTTP_ADAPTER_CLASS = LowSecHTTPAdapter

        permissive_browser = PermissiveBrowser()
        r = permissive_browser.open(url)
        self.assertEqual(r.status_code, 200)

        # No side effects.
        r = permissive_browser.open("http://example.org")
        self.assertEqual(r.status_code, 200)

        # change of ciphers is contextual, does not affect previous browser.
        self.assertRaises(requests.exceptions.SSLError, browser.open, url)
