# Copyright(C) 2026

# This file is part of woob.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

import responses

from .browser import OrangeBillBrowser


def test_login_establishes_access_session_before_profile_api():
    access_url = "https://login.orange.fr/api/access"
    login_url = "https://login.orange.fr/api/login"
    idme_error_location = "https://r.orange.fr/r/Oerreur_403?ref=euiidme"
    profile_url = "https://espace-client.orange.fr/ecd_wp/account/identification"

    def password_callback(request):
        if "orange_access=ready" not in request.headers.get("Cookie", ""):
            return (
                200,
                {"Content-Type": "application/json"},
                '{"nextStep":"redirect","data":{"location":"%s"}}' % idme_error_location,
            )
        return 200, {"Content-Type": "application/json"}, '{"step":"authenticated","data":{}}'

    def profile_callback(request):
        if "orange_access=ready" not in request.headers.get("Cookie", ""):
            return 401, {}, "Unauthorized"
        return 200, {"Content-Type": "application/json"}, '{"identity": {"name": "Test User"}}'

    with responses.RequestsMock(assert_all_requests_are_fired=True) as mocked:
        mocked.add(
            responses.GET,
            "https://login.orange.fr/?service=sosh&return_url=https://www.sosh.fr/",
            body="<html></html>",
            content_type="text/html",
        )
        mocked.add(
            responses.POST,
            access_url,
            json={},
            headers={"Set-Cookie": "orange_access=ready; Domain=.orange.fr; Path=/"},
        )
        mocked.add(responses.POST, login_url, json={})
        mocked.add_callback(responses.POST, "https://login.orange.fr/api/password", callback=password_callback)
        mocked.add(responses.GET, "https://www.orange.fr/portail", body="<html></html>", content_type="text/html")
        mocked.add_callback(responses.GET, profile_url, callback=profile_callback)

        browser = OrangeBillBrowser(None, "test-login", "test-password")
        browser.do_login()
        browser.profile_api_par.go(
            headers={
                "x-orange-caller-id": "ECQ",
                "accept": "application/vnd.mason+json",
            }
        )
        urls = [call.request.url for call in mocked.calls]
        assert urls.index(access_url) < urls.index(login_url)
        assert urls.index(access_url) < urls.index(profile_url)
