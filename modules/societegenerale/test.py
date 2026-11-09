# Copyright(C) 2010-2011 Jocelyn Jaubert
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

import json
from unittest.mock import Mock

import factory
import pytest

from woob.capabilities import NotLoaded
from woob.capabilities.profile import Person
from woob.tools.test import BackendTest

from .browser import SocieteGenerale
from .pages.accounts_list import ContactDetailsPage


class JSONFactory(factory.base.DictFactory):
    """Generates JSON encoded payloads.

    Use as a shortcut of a regular DictFactory followed by :func:`json.dumps` call.
    """

    @classmethod
    def _generate(cls, strategy, params):
        obj = super()._generate(strategy, params)
        return json.dumps(obj)


class SGStatusFactory(factory.base.DictFactory):
    """Build `commun` status dict for SG JSON payloads."""

    statut = "OK"
    raison = None
    action = None
    origine = "mpl"  # From URL


class TelephoneFactory(factory.base.DictFactory):
    """Build `telephones` for SG JSON payloads."""

    numero = factory.Faker("phone_number")
    type = factory.Iterator(["F"])
    nature = factory.Iterator(["MOBILE", "DOMICILE", "PROFESSIONEL"])
    valide = True
    error = False


class ConsultationAuthsecFactory(JSONFactory):
    """Build consultation-authsec.json payloads."""

    commun = factory.SubFactory(SGStatusFactory)
    donnees = factory.Dict(
        {
            "email": factory.Faker("email"),
            "adresse": factory.Dict(
                {
                    "numeroEtVoie": factory.Faker("street_address"),
                    "complementAdresse1": None,
                    "complementAdresse2": None,
                    "serviceDistribution": factory.Faker("city"),
                    "codePostal": factory.Faker("postcode"),
                    "ville": factory.Faker("city"),
                    "pays": factory.Faker("country"),
                }
            ),
            "detailsTelephones": factory.Dict(
                {
                    "natureTelephonePrivilegie": factory.Iterator(["MOBILE", "DOMICILE", "PROFESSIONEL"]),
                    "telephones": factory.List(
                        [
                            factory.SubFactory(TelephoneFactory, nature="MOBILE"),
                            factory.SubFactory(TelephoneFactory, nature="DOMICILE"),
                            factory.SubFactory(TelephoneFactory, nature="PROFESSIONEL"),
                        ]
                    ),
                }
            ),
        }
    )
    caract = {}


@pytest.fixture
def sg_par_browser(faker) -> SocieteGenerale:
    config = {
        "login": faker.bothify("########"),
        "password": faker.bothify("######"),
        "website": "par",
    }
    return SocieteGenerale(config, config["login"], config["password"])


# Unittests


@pytest.mark.parametrize(
    "payload", [ConsultationAuthsecFactory(), ConsultationAuthsecFactory(donnees__adresse__codePostal=None)]
)
def test_load_address(sg_par_browser, payload):
    """Postal address parses correctly.

    See https://gitlab.com/woob/woob/-/work_items/818
    """
    page = ContactDetailsPage(sg_par_browser, Mock("response", url="", text=payload))
    profile = Person()
    page.fill_profile(profile)
    assert profile.postal_address is not None
    assert profile.address is NotLoaded


# Integration tests


class SocieteGeneraleTest(BackendTest):
    MODULE = "societegenerale"

    def test_societegenerale(self):
        l = list(self.backend.iter_accounts())
        self.assertTrue(len(l) > 0)
        a = l[0]
        list(self.backend.iter_coming(a))
        list(self.backend.iter_history(a))
