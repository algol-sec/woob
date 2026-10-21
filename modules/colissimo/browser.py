# Copyright(C) 2013-2014      Florent Fourcot
#
# This file is part of a woob module.
#
# This woob module is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This woob module is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this woob module. If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

from typing import Any

from woob.browser import URL, PagesBrowser
from woob.browser.elements import DictElement, ItemElement, ListElement, method
from woob.browser.filters.json import Dict
from woob.browser.filters.standard import Coalesce, DateTime
from woob.browser.pages import HTMLPage, JsonPage
from woob.browser.profiles import Firefox
from woob.capabilities.base import NotAvailable
from woob.capabilities.parcel import Event, Parcel


__all__ = ["ColissimoBrowser"]


class MainPage(HTMLPage):
    pass


STATUSES_MAP = {
    "AARNAT": Parcel.STATUS_IN_TRANSIT,  # in transit to local distribution site
    "ACHNAT": Parcel.STATUS_IN_TRANSIT,  # in transit
    "ACHIECA": Parcel.STATUS_IN_TRANSIT,  # error in transit
    "EDRCOU": Parcel.STATUS_IN_TRANSIT,  # parcel collected by transporter
    "DISTOU": Parcel.STATUS_IN_TRANSIT,  # at local distribution site
    "DISIRST": Parcel.STATUS_IN_TRANSIT,  # could not deliver, retrying
    "DISIECIN": Parcel.STATUS_IN_TRANSIT,  # could not deliver, receipt in mailbox
    "DISINS": Parcel.STATUS_IN_TRANSIT,  # on hold at local distribution site
    "DESLIVD": Parcel.STATUS_ARRIVED,  # parcel delivered
    "DESLIVHD": Parcel.STATUS_ARRIVED,  # parcel delivered
    "DI1": Parcel.STATUS_ARRIVED,  # parcel delivered
    "DR1": Parcel.STATUS_PLANNED,
}


class TrackingPage(JsonPage):

    @method
    class iter_parcels(ListElement):
        def find_elements(self) -> dict[str, Any]:
            """Iterate JSON list."""
            yield from self.el

        class item(ItemElement):
            item_xpath = "shipment"
            klass = Parcel

            obj_arrival = Coalesce(
                DateTime(Dict("deliveryDate", default=NotAvailable)),
                DateTime(Dict("estimDate", default=NotAvailable)),
            )

            def obj_status(self):
                """Parcel status.

                Chronopost currentState does not have code, use shortLabel as fallback.
                """
                code = Dict("currentState/code", default=None)(self)
                if code:
                    return STATUSES_MAP.get(code, Parcel.STATUS_UNKNOWN)

                if Dict("currentState/shortLabel")(self) == "Livré":
                    return Parcel.STATUS_ARRIVED

                return Parcel.STATUS_UNKNOWN

            obj_info = Dict("currentState")

            class obj_history(DictElement):
                item_xpath = "event"

                class item(ItemElement):
                    klass = Event

                    obj_date = DateTime(Dict("date"))
                    obj_activity = Dict("label")
                    obj_location = Dict("country", default=None)


class ColissimoBrowser(PagesBrowser):
    BASEURL = "https://www.laposte.fr"
    PROFILE = Firefox()

    main_url = URL(r"/outils/suivre-vos-envois\?code=(?P<_id>.*)", MainPage)
    tracking_url = URL(r"/ssu/sun/back/suivi-unifie/(?P<_id>.*)\?lang=fr", TrackingPage)

    def get_tracking_info(self, _id: str) -> Parcel:
        self.main_url.stay_or_go(_id=_id)
        self.tracking_url.stay_or_go(_id=_id, headers={"Accept": "application/json"})
        return next(self.page.iter_parcels(_id))
