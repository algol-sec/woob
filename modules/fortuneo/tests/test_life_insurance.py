import json
from datetime import date
from decimal import Decimal
from unittest import TestCase

from woob.capabilities.bank import Transaction

from ..pages.accounts_list import (
    LifeInsuranceTransactionApiPage,
    LifeInsuranceTransactionDetailApiPage,
)


class MockBrowser:
    logger = None


def mock_json_response(url, payload):
    return type(
        "MockResponse",
        (),
        {
            "url": url,
            "headers": {"content-type": "application/json; charset=utf-8"},
            "text": json.dumps(payload),
        },
    )()


class TestLifeInsuranceTransactionApiPage(TestCase):
    """Listing page: each JSON entry becomes a Transaction with a mapped type and a detail link when available."""

    def test_iter_history(self):
        """A distribution is a profit and keeps its detail link, a partial surrender is a withdrawal without one."""
        data = {
            "transactionDetails": [
                {
                    "transactionId": "42",
                    "availableDetailOperation": True,
                    "transactionCode": "405",
                    "transactionLabel": "DISTRIBUTION",
                    "transactionNetAmount": "12.34",
                    "transactionDate": "2026-03-15",
                    "transactionCreationDate": "2026-03-16",
                },
                {
                    "transactionId": "43",
                    "availableDetailOperation": False,
                    "transactionCode": "360",
                    "transactionLabel": "RACHAT PARTIEL",
                    "transactionNetAmount": "-500.00",
                    "transactionDate": "2026-04-01",
                    "transactionCreationDate": "2026-04-02",
                },
            ],
        }
        response = mock_json_response(
            "https://mabanque.fortuneo.fr/valorisation-assurance-vie/api/life-insurance/accounts/123/transaction",
            data,
        )

        page = LifeInsuranceTransactionApiPage(MockBrowser(), response)
        transactions = list(page.iter_history())

        self.assertEqual(len(transactions), 2)

        distribution = transactions[0]
        self.assertEqual(distribution.label, "DISTRIBUTION")
        self.assertEqual(distribution.raw, "DISTRIBUTION")
        self.assertEqual(distribution.amount, Decimal("12.34"))
        self.assertEqual(distribution.date, date(2026, 3, 16))
        self.assertEqual(distribution.rdate, date(2026, 3, 15))
        self.assertEqual(distribution.type, Transaction.TYPE_PROFIT)
        self.assertEqual(distribution._details_link, "42")

        surrender = transactions[1]
        self.assertEqual(surrender.amount, Decimal("-500.00"))
        self.assertEqual(surrender.type, Transaction.TYPE_WITHDRAWAL)
        self.assertFalse(surrender._details_link)


class TestLifeInsuranceTransactionDetailApiPage(TestCase):
    """Detail page: the payload section depends on the operation kind and fills the transaction investments."""

    def test_fill_transaction_details_distribution(self):
        """A distribution fills one investment per distributed fund and updates the transaction date."""
        data = {
            "transactionDate": "2026-03-15",
            "distribution": {
                "fundsList": [
                    {
                        "label": "FONDS EURO",
                        "isinCode": "FR0000989626",
                        "netAssetValue": "10.50",
                        "amount": "12.34",
                        "valuationLastDate": "2026-03-14",
                        "entryShareNumber": "1.1752",
                    },
                ],
            },
        }
        response = mock_json_response(
            "https://mabanque.fortuneo.fr/valorisation-assurance-vie/api/life-insurance/accounts/123/transaction/42",
            data,
        )

        page = LifeInsuranceTransactionDetailApiPage(MockBrowser(), response)
        transaction = Transaction()
        transaction.rdate = date(2000, 1, 1)
        page.fill_transaction_details(transaction)

        self.assertEqual(len(transaction.investments), 1)
        inv = transaction.investments[0]
        self.assertEqual(inv.label, "FONDS EURO")
        self.assertEqual(inv.code, "FR0000989626")
        self.assertEqual(inv.code_type, "ISIN")
        self.assertEqual(inv.unitvalue, Decimal("10.50"))
        self.assertEqual(inv.valuation, Decimal("12.34"))
        self.assertEqual(inv.vdate, date(2026, 3, 14))
        self.assertEqual(inv.quantity, Decimal("1.1752"))
        self.assertEqual(transaction._code, "FR0000989626")
        self.assertEqual(transaction.rdate, date(2026, 3, 15))

    def test_fill_transaction_details_reallocation(self):
        """A reallocation yields a bought investment and a sold one, the sold quantity being negative."""
        data = {
            "reallocation": {
                "ingoingFundsList": [
                    {
                        "label": "UNITE DE COMPTE",
                        "isinCode": "FR0000000001",
                        "netAssetValue": "100.00",
                        "amount": "500.00",
                        "valuationLastDate": "2026-05-02",
                        "entryShareNumber": "5",
                    },
                ],
                "outgoingFundsList": [
                    {
                        "label": "AUTRE FONDS",
                        "isinCode": "FR0000000002",
                        "netAssetValue": "50.00",
                        "amount": "500.00",
                        "valuationLastDate": "2026-05-02",
                        "entryShareNumber": "10",
                    },
                ],
            },
        }
        response = mock_json_response(
            "https://mabanque.fortuneo.fr/valorisation-assurance-vie/api/life-insurance/accounts/123/transaction/44",
            data,
        )

        page = LifeInsuranceTransactionDetailApiPage(MockBrowser(), response)
        transaction = Transaction()
        page.fill_transaction_details(transaction)

        self.assertEqual(len(transaction.investments), 2)

        bought, sold = transaction.investments
        self.assertEqual(bought.quantity, Decimal("5"))
        self.assertEqual(bought.valuation, Decimal("500.00"))

        self.assertEqual(sold.quantity, Decimal("-10"))
        self.assertEqual(sold.valuation, Decimal("-500.00"))

    def test_fill_transaction_details_yearly_revaluation(self):
        """A yearly revaluation uses the profit sharing amount of the revaluation object."""
        data = {
            "yearlyRevolarisation": {
                "concernedFund": {
                    "label": "FONDS EURO",
                    "isinCode": "FR0000989626",
                    "netAssetValue": "110.00",
                    "amount": "0",
                    "valuationLastDate": "2026-12-31",
                    "entryShareNumber": "3",
                },
                "contractProfitSharingAmount": "77.00",
            },
        }
        response = mock_json_response(
            "https://mabanque.fortuneo.fr/valorisation-assurance-vie/api/life-insurance/accounts/123/transaction/45",
            data,
        )

        page = LifeInsuranceTransactionDetailApiPage(MockBrowser(), response)
        transaction = Transaction()
        page.fill_transaction_details(transaction)

        self.assertEqual(len(transaction.investments), 1)
        inv = transaction.investments[0]
        self.assertEqual(inv.valuation, Decimal("77.00"))
        self.assertEqual(inv.quantity, Decimal("3"))

    def test_fill_transaction_details_yearly_revaluation_root_amount(self):
        """When the revaluation object has no amount, the one at the root of the payload is used."""
        data = {
            "contractProfitSharingAmount": "88.00",
            "yearlyRevolarisation": {
                "concernedFund": {
                    "label": "FONDS EURO",
                    "isinCode": "FR0000989626",
                    "netAssetValue": "110.00",
                    "amount": "0",
                    "valuationLastDate": "2026-12-31",
                    "entryShareNumber": "4",
                },
            },
        }
        response = mock_json_response(
            "https://mabanque.fortuneo.fr/valorisation-assurance-vie/api/life-insurance/accounts/123/transaction/45",
            data,
        )

        page = LifeInsuranceTransactionDetailApiPage(MockBrowser(), response)
        transaction = Transaction()
        page.fill_transaction_details(transaction)

        self.assertEqual(len(transaction.investments), 1)
        self.assertEqual(transaction.investments[0].valuation, Decimal("88.00"))

    def test_fill_transaction_details_empty(self):
        """A payload without any known section leaves the transaction without investments."""
        response = mock_json_response(
            "https://mabanque.fortuneo.fr/valorisation-assurance-vie/api/life-insurance/accounts/123/transaction/46",
            {},
        )

        page = LifeInsuranceTransactionDetailApiPage(MockBrowser(), response)
        transaction = Transaction()
        page.fill_transaction_details(transaction)

        self.assertEqual(transaction.investments, [])
        self.assertFalse(hasattr(transaction, "_code"))
