# Copyright(C) 2016      Edouard Lambert
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

import base64
import contextlib
import json
import re
from decimal import Decimal
from functools import wraps
from operator import attrgetter, itemgetter
from urllib.parse import urljoin

from woob.browser.browsers import APIBrowser
from woob.browser.exceptions import ClientError, HTTPNotFound
from woob.browser.filters.standard import CleanDecimal, Date, MapIn
from woob.capabilities.bank import Account, Transaction
from woob.capabilities.bank.wealth import Investment
from woob.capabilities.base import NotAvailable
from woob.exceptions import ActionNeeded, BrowserIncorrectPassword
from woob.tools.capabilities.bank.investments import is_isin_valid


def need_login(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.user_id is None:
            self.do_login()
        return func(self, *args, **kwargs)

    return wrapper


class YomoniBrowser(APIBrowser):
    BASEURL = "https://yomoni.fr/private/"

    FRONT_URL = "https://my.yomoni.fr/"

    # AWS Cognito user pool used by https://my.yomoni.fr, read from the front-end
    # configuration at login. These are only fallbacks if that fails.
    COGNITO_USER_POOL_ID = "eu-central-1_QWF3ts5qd"
    COGNITO_CLIENT_ID = "3j13mdv6j1hilb53thbf2bbk58"

    ACCOUNT_TYPES = {
        "assurance vie": Account.TYPE_LIFE_INSURANCE,
        "compte titre": Account.TYPE_MARKET,
        "pea": Account.TYPE_PEA,
        "per": Account.TYPE_PER,
    }

    TRANSACTION_TYPES = {
        "APPORT": Transaction.TYPE_DEPOSIT,
        "APPORT_PROGRAMME": Transaction.TYPE_DEPOSIT,
        "SOUSCRIPTION": Transaction.TYPE_DEPOSIT,
        "VERSEMENT": Transaction.TYPE_DEPOSIT,
        "FRAIS": Transaction.TYPE_BANK,
    }

    # Internal reallocations between supports, they do not change the balance
    IGNORED_TRANSACTION_TYPES = ("ARBITRAGE",)

    waiting_statuses = (
        "RETURN_CUSTOMER_SERVICE",
        "SUBSCRIPTION_STEP_2",
        "SUBSCRIPTION_STEP_3",
        "SUBSCRIPTION_STEP_4",
    )

    def __init__(self, username, password, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.username = username
        self.password = password
        self.user_id = None
        self.accounts = []
        self.investments = {}
        self.histories = {}

    def build_request(self, *args, **kwargs):
        """
        Build a JSON request, keeping the Content-Type given by the caller.

        The Yomoni API takes application/json, which APIBrowser always sets,
        but AWS Cognito requires application/x-amz-json-1.1 for the login.
        """
        headers = kwargs.get("headers") or {}
        content_type = headers.pop("Content-Type", None)
        request = super().build_request(*args, **kwargs)
        if content_type:
            request.headers["Content-Type"] = content_type
        return request

    def get_cognito_config(self):
        """
        Get the Cognito user pool and client ids from the my.yomoni.fr bundle.

        The front-end embeds the configuration of each environment, we look
        for the "aws" settings of the one whose frontUrl is my.yomoni.fr.
        """
        with contextlib.suppress(HTTPNotFound):
            index = self.open(self.FRONT_URL).text
            scripts = re.findall(r'<script[^>]+src="(/_expo/static/js/web/[^"]+\.js)"', index)
            # The configuration lives in the main bundle, look at it first
            scripts.sort(key=lambda src: not src.rsplit("/", 1)[1].startswith("index-"))
            for src in scripts:
                if match := re.search(
                    r"""frontUrl:\s*['"]https://my\.yomoni\.fr['"][^}]*?"""
                    r"""aws:\s*\{\s*userPoolId:\s*['"]([^'"]+)['"],\s*userPoolWebClientId:\s*['"]([^'"]+)['"]""",
                    self.open(urljoin(self.FRONT_URL, src)).text,
                ):
                    return match.groups()
        self.logger.warning("Could not find the Cognito configuration in the Yomoni front-end, using known values")
        return self.COGNITO_USER_POOL_ID, self.COGNITO_CLIENT_ID

    def do_login(self):
        """
        Log in with AWS Cognito, as the my.yomoni.fr front-end does.

        Cognito returns a JWT (IdToken), sent as a Bearer token to the Yomoni
        API. The Yomoni user id, needed in some API paths, is read from the
        "custom:yomoni_id" claim of that token.
        """
        user_pool_id, client_id = self.get_cognito_config()
        region = user_pool_id.split("_")[0]
        try:
            response = self.request(
                f"https://cognito-idp.{region}.amazonaws.com/",
                data={
                    "AuthFlow": "USER_PASSWORD_AUTH",
                    "ClientId": client_id,
                    "AuthParameters": {
                        "USERNAME": self.username,
                        "PASSWORD": self.password,
                    },
                },
                headers={
                    "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
                    "Content-Type": "application/x-amz-json-1.1",
                },
            )
        except ClientError as e:
            error = e.response.json()
            if error.get("__type") in ("NotAuthorizedException", "UserNotFoundException"):
                raise BrowserIncorrectPassword(error.get("message"))
            raise

        if "AuthenticationResult" not in response:
            raise ActionNeeded(
                locale="fr-FR",
                message=(
                    "Une authentification supplémentaire est requise sur le site Yomoni "
                    f"({response.get('ChallengeName')})."
                ),
            )

        id_token = response["AuthenticationResult"]["IdToken"]
        self.session.headers["Authorization"] = f"Bearer {id_token}"

        # The token is not verified, we only need to read its payload
        payload = id_token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        self.user_id = payload["custom:yomoni_id"]

    @need_login
    def iter_accounts(self):
        """
        Iterate over accounts, one per compartment of each contract.

        A contract (life insurance, PER...) can be split into several
        compartments, each with its own balance, investments and transactions.
        """
        if self.accounts:
            yield from self.accounts
            return

        waiting = False
        for contract in self.request("v2/contracts"):
            status = (contract["contract"].get("statut") or {}).get("statut")
            waiting = waiting or status in self.waiting_statuses

            # Check project in progress
            if not contract["contract"].get("numero") or not contract.get("openingDate"):
                continue

            compartments = sorted(
                self.request(f"v2/contracts/{contract['id']}/compartments"),
                key=itemgetter("createDate"),
            )
            for index, compartment in enumerate(compartments):
                account = self.build_account(contract, compartment, index, len(compartments) > 1)
                self.accounts.append(account)
                yield account

        if not self.accounts and waiting:
            raise ActionNeeded(
                locale="fr-FR",
                message="Le service client Yomoni est en attente d'un retour de votre part.",
            )

    def build_account(self, contract, compartment, index, several):
        """
        Build the account of a compartment.

        :param index: position of the compartment in the contract, oldest first
        :param several: whether the contract has more than one compartment
        """
        number = contract["contract"]["numero"]
        contract_label = " ".join(contract["typeContract"].split("_"))

        a = Account()
        # The oldest compartment keeps the contract number as id, to stay
        # consistent with the ids used before compartments existed.
        a.id = "".join(number.split())
        if index:
            a.id = f"{a.id}-{compartment['id'][:8]}"
        a.number = number
        a.label = contract_label
        if several:
            # Compartment name first, as account labels are often truncated
            a.label = f"{compartment['name']} - {contract_label}"
        a.type = MapIn(self, self.ACCOUNT_TYPES, Account.TYPE_UNKNOWN).filter(contract_label.lower())
        a.opening_date = Date(default=NotAvailable).filter(
            contract["openingDate"] if not index else compartment["createDate"][:10]
        )
        a.balance = round(Decimal(str(compartment["outstanding"])), 2)
        a.valuation_diff = round(Decimal(str(compartment["capitalGainLoss"])), 2)
        a.currency = "EUR"
        a.iban = NotAvailable
        if a.type in (Account.TYPE_MARKET, Account.TYPE_PEA):
            a.iban = (contract.get("rib") or {}).get("iban") or NotAvailable
        a._contract_id = contract["id"]
        a._compartment_id = compartment["id"]
        return a

    @need_login
    def iter_investment(self, account):
        if account.id not in self.investments:
            investments = [
                self.build_investment(inv)
                for inv in self.request(
                    f"contracts/{account._contract_id}/compartments/{account._compartment_id}/supports"
                )
            ]

            # Cash is not returned as a support, it is the remaining part of the balance
            liquidity = account.balance - sum(inv.valuation for inv in investments)
            if liquidity >= Decimal("0.01"):
                i = Investment()
                i.label = "Liquidités"
                i.code = "XX-liquidity"
                i.code_type = NotAvailable
                i.valuation = liquidity
                investments.append(i)

            self.investments[account.id] = investments
        return self.investments[account.id]

    def build_investment(self, inv):
        i = Investment()
        # If nothing is given to make the label, we use the ISIN instead
        labels = [label for label in (inv.get("classification"), inv.get("label")) if label]
        i.label = " - ".join(labels) or inv["isin"]

        i.code = inv["isin"]
        if not is_isin_valid(i.code):
            i.code = NotAvailable
            i.code_type = NotAvailable
            if "Solde Espèces" in i.label:
                i.code = "XX-liquidity"
        else:
            i.code_type = Investment.CODE_TYPE_ISIN

        # Some supports have no share price (0 or null, e.g. euro funds or some
        # PER funds): only their valuation is meaningful then.
        if inv.get("sharePrice"):
            i.quantity = CleanDecimal(default=NotAvailable).filter(inv.get("numberOfShares"))
            i.unitvalue = CleanDecimal(default=NotAvailable).filter(inv["sharePrice"])
        i.valuation = round(Decimal(str(inv["outstanding"])), 2)
        i.diff = CleanDecimal(default=NotAvailable).filter(inv.get("capitalGainLoss"))
        if inv.get("capitalGainLossPercent") is not None:
            i.diff_ratio = Decimal(str(inv["capitalGainLossPercent"])) / 100
        if inv.get("distribution") is not None:
            i.portfolio_share = Decimal(str(inv["distribution"]))
        i.vdate = Date(default=NotAvailable).filter(inv.get("date"))
        return i

    @need_login
    def iter_transactions(self, account, with_history=True, with_coming=True):
        """
        Iterate over the movements of a compartment, booked and coming ones
        are returned by the same endpoint.
        """
        if account.id not in self.histories:
            histories = []
            movements = self.request(
                f"users/{self.user_id}/patrimoine/enveloppes/{account._contract_id}"
                f"/compartiments/{account._compartment_id}/mouvements"
            )
            for movement in movements:
                if movement["type"] in self.IGNORED_TRANSACTION_TYPES:
                    continue

                t = Transaction()
                t.label = " ".join(movement["type"].split("_")).capitalize()
                t.date = t.rdate = Date().filter(movement["dateEffet"])
                t.type = self.TRANSACTION_TYPES.get(movement["type"], Transaction.TYPE_BANK)
                t.amount = round(Decimal(str(movement["montantEuros"])), 2)
                t.coming = movement["statut"] != "TRAITE"
                histories.append(t)

            self.histories[account.id] = sorted(histories, key=attrgetter("date"), reverse=True)
        return [t for t in self.histories[account.id] if (with_coming if t.coming else with_history)]
