import re

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from ..models.barangay import barangay_matches_area, normalized_location_name


def normalized_locality_name(value):
    value = normalized_location_name(value)
    value = re.sub(r"^(city|municipality) of ", "", value)
    value = re.sub(r" city$", "", value)
    return value


class SetAddressWizard(models.TransientModel):
    _name = "dex.city.set.address.wizard"
    _description = "Set Proper Partner Address"

    partner_id = fields.Many2one("res.partner", readonly=True)
    sale_order_id = fields.Many2one("sale.order", readonly=True)
    parent_partner_id = fields.Many2one("res.partner", readonly=True)
    create_delivery_address = fields.Boolean(readonly=True)
    show_address_form = fields.Boolean(default=True, readonly=True)
    edit_delivery_address = fields.Boolean(default=False, readonly=True)
    delivery_name = fields.Char(string="Delivery Address Name")
    created_partner_id = fields.Many2one("res.partner", readonly=True)
    delivery_address_ids = fields.Many2many(
        "res.partner",
        compute="_compute_delivery_address_ids",
        string="Existing Delivery Addresses",
        readonly=True,
    )

    @api.depends(
        "partner_id",
        "partner_id.parent_id",
        "partner_id.child_ids",
        "parent_partner_id",
        "parent_partner_id.child_ids",
    )
    def _compute_delivery_address_ids(self):
        for wizard in self:
            parent = wizard.parent_partner_id
            if not parent and wizard.partner_id:
                parent = (
                    wizard.partner_id.parent_id
                    if wizard.partner_id.type in ("delivery", "invoice")
                    else wizard.partner_id
                )
            wizard.delivery_address_ids = (
                parent.child_ids.filtered(
                    lambda partner: partner.type in ("delivery", "invoice")
                )
                if parent
                else self.env["res.partner"]
            )

    country_id = fields.Many2one(
        "res.country", required=True, default=lambda self: self.env.ref("base.ph")
    )
    country_code = fields.Char(related="country_id.code", readonly=True)
    postal_supported = fields.Boolean(
        related="country_id.dex_geonames_postal_supported", readonly=True
    )
    use_manual_location = fields.Boolean(string="Enter Location Manually")
    use_city_selection = fields.Boolean(
        compute="_compute_use_city_selection", readonly=True
    )
    state_id = fields.Many2one(
        "res.country.state",
        string="State / Province",
        domain="[('country_id', '=', country_id), ('geonames_active', '=', True)]",
    )
    state_text = fields.Char(string="State / Province")
    city_id = fields.Many2one(
        "res.city",
        string="City / Municipality",
        domain="[('country_id', '=', country_id), ('state_id', '=', state_id), ('geonames_has_postal', '=', True), ('dex_is_district', '=', False)]",
    )
    district_id = fields.Many2one(
        "res.city",
        string="District",
        domain="[('dex_parent_city_id', '=', city_id), ('dex_is_district', '=', True)]",
    )
    has_districts = fields.Boolean(compute="_compute_location_options", readonly=True)
    show_area_selection = fields.Boolean(
        compute="_compute_location_options", readonly=True
    )
    city = fields.Char(string="City / Municipality")
    zip = fields.Char(string="ZIP")
    street = fields.Char(string="Address Line 1")
    street2 = fields.Char(string="Address Line 2")
    barangay_id = fields.Many2one(
        "dex.city.barangay",
        string="Barangay",
        domain="[('id', 'in', allowed_barangay_ids)]",
    )
    allowed_barangay_ids = fields.Many2many(
        "dex.city.barangay",
        compute="_compute_allowed_barangay_ids",
        readonly=True,
    )
    show_barangay_selection = fields.Boolean(
        compute="_compute_allowed_barangay_ids",
        readonly=True,
    )
    barangay_area_auto_selected = fields.Boolean(
        string="Barangay Area Auto-Selected", readonly=True
    )
    postal_code_id = fields.Many2one(
        "dex.city.postal.code",
        string="Area",
        domain="[('country_id', '=', country_id), ('state_id', '=?', state_id), ('city_id', '=?', city_id), ('dex_district_id', '=?', district_id), ('active', '=', True)]",
    )
    area_name = fields.Char(related="postal_code_id.place_name", readonly=True)
    district_name = fields.Char(related="district_id.name", readonly=True)
    full_address = fields.Text(
        string="Complete Address", compute="_compute_full_address", readonly=True
    )

    def _validate_address_text(self, value, label, required=False):
        text = (value or "").strip()
        if not text:
            if required:
                raise ValidationError(_("%s is required.") % label)
            return

        normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
        compact = normalized.replace(" ", "")
        invalid_values = {
            "na",
            "none",
            "null",
            "nil",
            "unknown",
            "notapplicable",
            "noaddress",
            "tbd",
            "test",
            "sample",
            "dummy",
            "placeholder",
            "asdf",
            "qwerty",
            "xxx",
            "xxxx",
        }
        invalid_phrases = re.compile(
            r"(^|\b)(n\s*/?\s*a|none|null|nil|unknown|not\s+applicable|"
            r"no\s+address|tbd|test|sample|dummy|placeholder)(\b|$)",
            re.IGNORECASE,
        )
        if (
            not normalized
            or compact in invalid_values
            or invalid_phrases.search(text)
            or (compact and len(set(compact)) == 1)
        ):
            raise ValidationError(
                _(
                    "%s must contain a real address, not punctuation or a "
                    "placeholder value."
                )
                % label
            )

        if required and (len(compact) < 4 or not re.search(r"[A-Za-z]{2,}", text)):
            raise ValidationError(
                _("%s must contain meaningful street or building details.") % label
            )

    @api.depends(
        "street",
        "street2",
        "barangay_id",
        "city_id",
        "district_id",
        "city",
        "state_id",
        "state_text",
        "postal_code_id",
        "zip",
        "country_id",
    )
    def _compute_full_address(self):
        for wizard in self:
            city_name = wizard.city_id.name if wizard.city_id else wizard.city
            parts = []
            for value in [wizard.street, wizard.street2]:
                if value and value.strip():
                    parts.append(value.strip().rstrip(", "))
            area_name = wizard.postal_code_id.place_name
            if wizard.barangay_id:
                barangay_name = wizard.barangay_id.name
                if area_name:
                    parts.append("%s - %s" % (area_name, barangay_name))
                else:
                    parts.append(barangay_name)
            elif area_name:
                parts.append(area_name)
            if wizard.district_id and wizard.district_id.name != area_name:
                parts.append(wizard.district_id.name)
            if city_name:
                parts.append(city_name.strip().rstrip(", "))
            state_name = wizard.state_id.name or wizard.state_text
            if state_name:
                province_zip = state_name
                if wizard.zip:
                    province_zip = "%s %s" % (province_zip, wizard.zip.strip())
                parts.append(province_zip)
            elif wizard.zip:
                parts.append(wizard.zip.strip())
            if wizard.country_id:
                parts.append(wizard.country_id.name)
            wizard.full_address = ", ".join(parts)

    @api.depends(
        "country_id",
        "country_id.code",
        "use_manual_location",
    )
    def _compute_use_city_selection(self):
        for wizard in self:
            wizard.use_city_selection = bool(
                wizard.country_id and not wizard.use_manual_location
            )

    @api.depends(
        "city_id",
        "district_id",
        "postal_supported",
        "use_manual_location",
    )
    def _compute_location_options(self):
        City = self.env["res.city"]
        Postal = self.env["dex.city.postal.code"]
        for wizard in self:
            wizard.has_districts = bool(
                wizard.city_id
                and City.search_count(
                    [
                        ("dex_parent_city_id", "=", wizard.city_id.id),
                        ("dex_is_district", "=", True),
                    ]
                )
            )
            if (
                not wizard.city_id
                or not wizard.postal_supported
                or wizard.use_manual_location
                or (wizard.has_districts and not wizard.district_id)
            ):
                wizard.show_area_selection = False
                continue
            wizard.show_area_selection = (
                Postal.search_count(wizard._postal_area_domain()) > 1
            )

    @api.depends("state_id", "city_id", "district_id", "postal_code_id")
    def _compute_allowed_barangay_ids(self):
        for wizard in self:
            barangays = wizard._matching_barangays()
            wizard.allowed_barangay_ids = barangays
            wizard.show_barangay_selection = bool(barangays)

    def _state_domain(self):
        self.ensure_one()
        if not self.country_id:
            return [("id", "=", False)]
        return [
            ("country_id", "=", self.country_id.id),
            ("geonames_active", "=", True),
        ]

    def _city_domain(self):
        self.ensure_one()
        if not self.country_id or not self.state_id:
            return [("id", "=", False)]
        return [
            ("country_id", "=", self.country_id.id),
            ("state_id", "=", self.state_id.id),
            ("geonames_has_postal", "=", True),
            ("dex_is_district", "=", False),
        ]

    def _district_domain(self):
        self.ensure_one()
        if not self.city_id:
            return [("id", "=", False)]
        return [
            ("dex_parent_city_id", "=", self.city_id.id),
            ("dex_is_district", "=", True),
        ]

    def _domain_result(self):
        self.ensure_one()
        return {
            "domain": {
                "state_id": self._state_domain(),
                "city_id": self._city_domain(),
                "district_id": self._district_domain(),
                "postal_code_id": self._postal_area_domain(),
                "barangay_id": self._barangay_domain(),
            }
        }

    def _base_barangay_domain(self):
        self.ensure_one()
        if not self.state_id or not (self.district_id or self.city_id):
            return [("id", "=", False)]
        domain = [("state_id", "=", self.state_id.id)]
        location = self.district_id or self.city_id
        if not location.psgc_code:
            return [("id", "=", False)]
        domain += [
            "|",
            ("parent_psgc_code", "=", location.psgc_code),
            ("city_psgc_code", "=", location.psgc_code),
        ]
        return domain

    def _matching_barangays(self):
        self.ensure_one()
        barangays = self.env["dex.city.barangay"].search(self._base_barangay_domain())
        if not self.postal_code_id:
            return barangays
        explicitly_linked = self.postal_code_id.barangay_ids & barangays
        if explicitly_linked:
            return explicitly_linked
        area_name = self.postal_code_id.place_name
        matching = barangays.filtered(
            lambda barangay: barangay_matches_area(barangay.name, area_name)
        )
        if matching:
            return matching
        locality = self.district_id or self.city_id
        if locality and normalized_locality_name(area_name) == normalized_locality_name(
            locality.name
        ):
            return barangays
        return barangays.browse()

    def _barangay_domain(self):
        self.ensure_one()
        return [("id", "in", self._matching_barangays().ids)]

    def _postal_area_domain(self):
        self.ensure_one()
        if not self.city_id:
            return [("id", "=", False)]
        domain = [
            ("country_id", "=", self.country_id.id),
            ("state_id", "=", self.state_id.id),
            ("city_id", "=", self.city_id.id),
            ("active", "=", True),
        ]
        if self.district_id:
            domain.append(("dex_district_id", "=", self.district_id.id))
        elif self.env["res.city"].search_count(
            [("dex_parent_city_id", "=", self.city_id.id)]
        ):
            domain.append(("id", "=", False))
        else:
            domain.append(("dex_district_id", "=", False))
        return domain

    def _auto_select_area(self):
        self.ensure_one()
        self.barangay_area_auto_selected = False
        self.postal_code_id = False
        if not self.city_id or not self.postal_supported:
            self.zip = self.city_id.zipcode if self.city_id else False
            return
        postal_codes = self.env["dex.city.postal.code"].search(
            self._postal_area_domain(), order="postal_code, place_name"
        )
        if len(postal_codes) == 1:
            self.postal_code_id = postal_codes
            self.zip = postal_codes.postal_code
        else:
            self.zip = self.city_id.zipcode if not postal_codes else False

    def _auto_select_area_from_text(self):
        self.ensure_one()
        if self.postal_code_id or not self.city_id or not self.postal_supported:
            return
        text = " ".join(
            value.strip().lower()
            for value in (self.street, self.street2)
            if value and value.strip()
        )
        if self.barangay_id:
            text = "%s %s" % (text, self.barangay_id.name.lower())
        text = re.sub(r"[^a-z0-9]+", " ", text).strip()
        if not text:
            return
        postal_codes = self.env["dex.city.postal.code"].search(
            self._postal_area_domain(), order="postal_code, place_name"
        )
        matching = postal_codes.filtered(
            lambda postal: (
                re.sub(r"[^a-z0-9]+", " ", postal.place_name.lower()).strip()
                and re.sub(r"[^a-z0-9]+", " ", postal.place_name.lower()).strip()
                in text
            )
        )
        if len(set(matching.mapped("postal_code"))) == 1:
            self.postal_code_id = matching[:1]
            self.zip = matching[:1].postal_code

    @api.onchange("country_id")
    def _onchange_country_id(self):
        if self.state_id and self.state_id.country_id != self.country_id:
            self.state_id = False
        if self.city_id and self.city_id.country_id != self.country_id:
            self.city_id = False
            self.district_id = False
            self.city = False
            self.zip = False
        if self.country_id.code != "PH":
            self.barangay_id = False
            self.use_manual_location = not (
                self.country_id.dex_geonames_postal_supported
                and bool(
                    self.env["res.city"].search_count(
                        [
                            ("country_id", "=", self.country_id.id),
                            ("geonames_active", "=", True),
                            ("geonames_has_postal", "=", True),
                        ]
                    )
                )
            )
        else:
            self.use_manual_location = False
            self.state_text = False
        self.postal_code_id = False
        self.district_id = False
        return self._domain_result()

    @api.onchange("use_manual_location")
    def _onchange_use_manual_location(self):
        if self.country_code == "PH":
            self.use_manual_location = False
        if self.use_manual_location:
            self.city_id = False
            self.district_id = False
            self.postal_code_id = False
        else:
            self.state_text = False
        return self._domain_result()

    @api.onchange("state_id")
    def _onchange_state_id(self):
        if self.city_id and self.city_id.state_id != self.state_id:
            self.city_id = False
            self.district_id = False
            self.city = False
            self.zip = False
        if self.barangay_id and self.barangay_id.state_id != self.state_id:
            self.barangay_id = False
        if self.postal_code_id and self.postal_code_id.state_id != self.state_id:
            self.postal_code_id = False
        return self._domain_result()

    @api.onchange("city_id")
    def _onchange_city_id(self):
        self.district_id = False
        if self.city_id:
            self.country_id = self.city_id.country_id
            self.state_id = self.city_id.state_id
            self.city = self.city_id.name
        self._auto_select_area()
        self._auto_select_area_from_text()
        if self.barangay_id and not self.env["dex.city.barangay"].search_count(
            self._barangay_domain() + [("id", "=", self.barangay_id.id)]
        ):
            self.barangay_id = False
        return self._domain_result()

    @api.onchange("district_id")
    def _onchange_district_id(self):
        self.barangay_id = False
        self._auto_select_area()
        self._auto_select_area_from_text()
        return self._domain_result()

    @api.onchange("street", "street2")
    def _onchange_address_text(self):
        self._auto_select_area_from_text()
        return self._domain_result()

    @api.onchange("postal_code_id")
    def _onchange_postal_code_id(self):
        self.barangay_area_auto_selected = False
        if self.postal_code_id:
            self.country_id = self.postal_code_id.country_id
            self.state_id = self.postal_code_id.state_id
            if self.postal_code_id.city_id:
                self.city_id = self.postal_code_id.city_id
                self.city = self.postal_code_id.city_id.name
            if self.postal_code_id.dex_district_id:
                self.district_id = self.postal_code_id.dex_district_id
            elif not self.city_id and not self.use_city_selection:
                self.city = self.postal_code_id.place_name
            self.zip = self.postal_code_id.postal_code
        elif self.postal_supported and not self.use_manual_location:
            self.zip = False
        barangay_domain = self._barangay_domain()
        if self.barangay_id and not self.env["dex.city.barangay"].search_count(
            barangay_domain + [("id", "=", self.barangay_id.id)]
        ):
            self.barangay_id = False
        return self._domain_result()

    @api.onchange("barangay_id")
    def _onchange_barangay_id(self):
        if not self.barangay_id:
            if self.barangay_area_auto_selected:
                self.postal_code_id = False
                self.zip = False
                self.barangay_area_auto_selected = False
            return self._domain_result()
        if self.postal_code_id:
            return self._domain_result()
        barangay_name = re.sub(
            r"[^a-z0-9]+", " ", self.barangay_id.name.lower()
        ).strip()
        postal_codes = self.env["dex.city.postal.code"].search(
            self._postal_area_domain(), order="postal_code, place_name"
        )
        matching = postal_codes.filtered(
            lambda postal: re.sub(r"[^a-z0-9]+", " ", postal.place_name.lower()).strip()
            == barangay_name
        )
        if not matching:
            barangay_tokens = set(barangay_name.split())

            def contains_barangay(postal):
                segments = re.split(r"\s*(?:-|/|&)\s*", postal.place_name or "")
                for segment in segments:
                    normalized_segment = re.sub(
                        r"[^a-z0-9]+", " ", segment.lower()
                    ).strip()
                    if normalized_segment == barangay_name:
                        return True
                    if set(normalized_segment.split()) == barangay_tokens:
                        return True
                return False

            matching = postal_codes.filtered(contains_barangay)
        if len(set(matching.mapped("postal_code"))) == 1:
            self.postal_code_id = matching[:1]
            self.zip = matching[:1].postal_code
            self.barangay_area_auto_selected = True
        return self._domain_result()

    def action_open_new_delivery_address(self):
        self.ensure_one()
        if not self.env.user.has_group("sales_team.group_sale_salesman"):
            raise AccessError(_("Only Salespersons can create delivery addresses."))
        parent = self.parent_partner_id
        if not parent and self.partner_id:
            parent = (
                self.partner_id.parent_id
                if self.partner_id.type in ("delivery", "invoice")
                else self.partner_id
            )
        if not parent:
            raise ValidationError(
                _("A customer is required to create a delivery address.")
            )
        country = parent.country_id or self.env.ref("base.ph")
        wizard = self.create(
            {
                "sale_order_id": self.sale_order_id.id,
                "partner_id": parent.id,
                "parent_partner_id": parent.id,
                "create_delivery_address": True,
                "country_id": country.id,
            }
        )
        wizard_view = self.env.ref("dex_city.view_set_address_wizard_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("Create Delivery Address"),
            "res_model": "dex.city.set.address.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "view_id": wizard_view.id,
            "views": [(wizard_view.id, "form")],
            "target": "new",
        }

    def action_start_create_delivery_address(self):
        self.ensure_one()
        if not self.env.user.has_group("sales_team.group_sale_salesman"):
            raise AccessError(_("Only Salespersons can create delivery addresses."))
        self.show_address_form = True
        return False

    def action_apply(self):
        self.ensure_one()
        if self.create_delivery_address:
            if not self.env.user.has_group("sales_team.group_sale_salesman"):
                raise AccessError(_("Only Salespersons can create delivery addresses."))
        elif not self.env.user.has_group("dex_city.group_delivery_address_manager"):
            raise AccessError(
                _("You do not have permission to change a partner address.")
            )
        if self.create_delivery_address:
            delivery_name = (self.delivery_name or "").strip()
            parent_partner = self.parent_partner_id or self.partner_id
            if not delivery_name:
                raise ValidationError(_("Delivery Address Name is required."))
            if not self.sale_order_id or not parent_partner:
                raise ValidationError(
                    _(
                        "The Sale Order customer is required to create a delivery address."
                    )
                )
        else:
            parent_partner = False
            if not self.partner_id:
                raise ValidationError(_("A partner is required to set the address."))

        if (
            self.city_id
            and self.postal_supported
            and not self.use_manual_location
            and not self.postal_code_id
        ):
            entered_zip = self.zip
            self._auto_select_area()
            if entered_zip:
                self.zip = entered_zip

        if (
            self.create_delivery_address
            and self.country_code == "PH"
            and self.postal_supported
            and not self.use_manual_location
        ):
            if not self.postal_code_id:
                raise ValidationError(
                    _(
                        "Select the Area so the ZIP code can be determined automatically."
                    )
                )
            if not self.zip:
                self.zip = self.postal_code_id.postal_code

        self._validate_address_text(
            self.street, _("Address Line 1"), required=True
        )
        self._validate_address_text(self.street2, _("Address Line 2"), required=False)
        missing = [(_("Country"), self.country_id), (_("Address Line 1"), self.street)]
        if self.country_code == "PH":
            missing.append((_("State / Province"), self.state_id))
        if (
            self.postal_supported
            and not self.use_manual_location
            and self.show_area_selection
        ):
            missing.append((_("Area"), self.postal_code_id))
        if self.has_districts:
            missing.append((_("District"), self.district_id))
        missing = [label for label, value in missing if not value]
        if (
            self.country_code == "PH"
            and self.show_barangay_selection
            and not self.barangay_id
        ):
            missing.append(_("Barangay"))
        if missing:
            raise ValidationError(
                _("Complete the following address fields: %s") % ", ".join(missing)
            )

        city_name = self.city_id.name if self.city_id else self.city
        if self.use_city_selection and not self.city_id:
            raise ValidationError(
                _("Select a City / Municipality from the official list.")
            )
        if not city_name:
            raise ValidationError(_("City / Municipality is required."))
        if self.city_id and (
            self.city_id.country_id != self.country_id
            or self.city_id.state_id != self.state_id
        ):
            raise ValidationError(
                _(
                    "The selected city does not belong to the selected state and country."
                )
            )
        if self.district_id and (
            self.district_id.dex_parent_city_id != self.city_id
            or not self.district_id.dex_is_district
        ):
            raise ValidationError(
                _("The selected district does not belong to the selected city.")
            )
        if self.postal_code_id and (
            self.postal_code_id.country_id != self.country_id
            or self.postal_code_id.state_id != self.state_id
        ):
            raise ValidationError(
                _(
                    "The selected area does not belong to the selected state and country."
                )
            )
        if self.postal_code_id.city_id and (
            self.postal_code_id.city_id != self.city_id
        ):
            raise ValidationError(
                _("The selected area does not belong to the selected city.")
            )
        if self.postal_code_id and (
            self.postal_code_id.dex_district_id != self.district_id
        ):
            raise ValidationError(
                _("The selected area does not belong to the selected district.")
            )
        if self.barangay_id and self.barangay_id.state_id != self.state_id:
            raise ValidationError(
                _("The selected barangay does not belong to the selected state.")
            )
        if self.barangay_id and not self.env["dex.city.barangay"].search_count(
            self._barangay_domain() + [("id", "=", self.barangay_id.id)]
        ):
            raise ValidationError(
                _("The selected barangay does not belong to the selected area.")
            )
        if (
            self.barangay_id
            and (self.district_id or self.city_id).psgc_code
            and not (
                self.barangay_id.parent_psgc_code
                == (self.district_id or self.city_id).psgc_code
                or self.barangay_id.city_psgc_code
                == (self.district_id or self.city_id).psgc_code
            )
        ):
            raise ValidationError(
                _(
                    "The selected barangay does not belong to the selected district, city, or municipality."
                )
            )

        full_address = self.full_address.strip()

        address_complete = bool(self.country_id and self.street and city_name)
        if self.country_code == "PH":
            address_complete = address_complete and bool(self.state_id and self.zip)
        if self.show_area_selection:
            address_complete = address_complete and bool(self.postal_code_id)
        if self.has_districts:
            address_complete = address_complete and bool(self.district_id)
        if self.show_barangay_selection:
            address_complete = address_complete and bool(self.barangay_id)
        address_values = {
            "street": self.street.strip() if self.street else False,
            "street2": self.street2.strip() if self.street2 else False,
            "city_id": False,
            "city": city_name,
            "state_id": self.state_id.id if self.state_id else False,
            "zip": self.zip.strip() if self.zip else False,
            "country_id": self.country_id.id,
            "dex_address_line1": self.street.strip() if self.street else False,
            "dex_address_line2": self.street2.strip() if self.street2 else False,
            "dex_barangay_id": self.barangay_id.id,
            "dex_state_text": self.state_text.strip() if self.state_text else False,
            "dex_postal_code_id": self.postal_code_id.id,
            "dex_district_id": self.district_id.id,
            "is_proper_address_set": address_complete,
        }

        if self.create_delivery_address:
            self.env.cr.execute(
                "SELECT id FROM %s WHERE id = %%s FOR UPDATE" % self._table,
                [self.id],
            )
            self.invalidate_cache(["created_partner_id"], [self.id])
            partner = self.created_partner_id.exists()
            if not partner:
                partner = self.env["res.partner"].create(
                    {
                        "name": delivery_name,
                        "parent_id": parent_partner.id,
                        "type": "delivery",
                        "company_id": parent_partner.company_id.id,
                    }
                )
                self.created_partner_id = partner
            address_values["name"] = delivery_name
            partner.with_context(dex_city_validate_address=True).write(address_values)
            self.sale_order_id.write(
                {
                    "partner_shipping_id": partner.id,
                    "partner_delivery_id2": partner.id,
                    "street_address_name": partner.street or "",
                    "stree2_address_name": partner.street2 or "",
                    "city_name": partner.city or "",
                    "zip_code": partner.zip or "",
                }
            )
            return {"type": "ir.actions.client", "tag": "reload"}

        self.partner_id.with_context(dex_city_validate_address=True).write(
            address_values
        )
        required_wizard_id = self.env.context.get("dex_city_return_required_wizard_id")
        if required_wizard_id:
            required_wizard = (
                self.env["dex.city.proper.address.required.wizard"]
                .browse(required_wizard_id)
                .exists()
            )
            if required_wizard:
                return required_wizard.action_after_address_set()
        return {"type": "ir.actions.act_window_close"}
