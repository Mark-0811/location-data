from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

PROPER_ADDRESS_FIELDS = {
    "street",
    "street2",
    "city",
    "city_id",
    "state_id",
    "zip",
    "country_id",
    "dex_state_text",
    "dex_postal_code_id",
    "dex_district_id",
}


class ResPartner(models.Model):
    _inherit = "res.partner"
    city_id = fields.Many2one("res.city", string="City of Address", copy=True)
    street = fields.Char(required=False)
    is_proper_address_set = fields.Boolean(
        string="Proper Address Set",
        default=False,
        copy=False,
        readonly=True,
    )
    dex_customer_rank = fields.Integer(
        compute="_compute_dex_customer_rank",
        string="Customer Rank (Address Owner)",
    )
    dex_is_customer_address = fields.Boolean(
        compute="_compute_dex_is_customer_address",
        string="Customer Address",
    )
    dex_hide_customer_address_fields = fields.Boolean(
        compute="_compute_dex_hide_customer_address_fields",
        string="Hide Customer Address Fields",
    )

    @api.depends("customer_rank", "parent_id", "parent_id.customer_rank", "type")
    def _compute_dex_customer_rank(self):
        for partner in self:
            owner = (
                partner.parent_id
                if partner.parent_id and partner.type in ("delivery", "invoice")
                else partner
            )
            partner.dex_customer_rank = owner.customer_rank

    @api.depends("customer_rank", "parent_id", "parent_id.customer_rank", "type")
    def _compute_dex_is_customer_address(self):
        for partner in self:
            owner = (
                partner.parent_id
                if partner.parent_id and partner.type in ("delivery", "invoice")
                else partner
            )
            partner.dex_is_customer_address = owner.customer_rank > 0

    @api.depends("customer_rank", "parent_id", "parent_id.customer_rank", "type")
    def _compute_dex_hide_customer_address_fields(self):
        for partner in self:
            owner = (
                partner.parent_id
                if partner.parent_id and partner.type in ("delivery", "invoice")
                else partner
            )
            partner.dex_hide_customer_address_fields = owner.customer_rank > 0

    dex_address_line1 = fields.Char(copy=False)
    dex_address_line2 = fields.Char(copy=False)
    dex_barangay_id = fields.Many2one(
        "dex.city.barangay", copy=False, ondelete="set null"
    )
    dex_state_text = fields.Char(copy=False)
    dex_postal_code_id = fields.Many2one(
        "dex.city.postal.code", copy=False, ondelete="set null"
    )
    dex_district_id = fields.Many2one(
        "res.city", string="District", copy=False, ondelete="set null"
    )

    def _dex_address_lines(self):
        self.ensure_one()

        def clean(value):
            return (value or "").strip().strip(",")

        if self.is_proper_address_set and self.dex_address_line1:
            detail_parts = [
                clean(self.dex_address_line1),
                clean(self.dex_address_line2),
            ]
            area_name = clean(self.dex_postal_code_id.place_name)
            if self.dex_barangay_id:
                if area_name:
                    detail_parts.append(
                        "%s - %s" % (area_name, self.dex_barangay_id.name)
                    )
                else:
                    detail_parts.append(self.dex_barangay_id.name)
            elif area_name:
                detail_parts.append(area_name)
            lines = [", ".join(filter(None, detail_parts))]
        elif self.is_proper_address_set and self.street:
            return [clean(self.street)]
        else:
            lines = list(
                filter(
                    None,
                    [
                        clean(self.street),
                        clean(self.street2),
                    ],
                )
            )

        city_name = clean(self.city_id.name if self.city_id else self.city)
        state_name = clean(self.state_id.name if self.state_id else self.dex_state_text)
        zip_code = clean(self.zip)
        location_parts = []
        district_name = clean(self.dex_district_id.name)
        if district_name and district_name != city_name:
            location_parts.append(district_name)
        if city_name:
            location_parts.append(city_name)
        if state_name:
            location_parts.append(
                "%s %s" % (state_name, zip_code) if zip_code else state_name
            )
        elif zip_code:
            location_parts.append(zip_code)
        if location_parts:
            lines.append(", ".join(location_parts))
        if self.country_id:
            lines.append(self.country_id.name)
        return list(filter(None, lines))

    @api.model
    def _fields_view_get_address(self, arch):
        arch = super(ResPartner, self)._fields_view_get_address(arch)
        doc = etree.fromstring(arch)
        address_divs = doc.xpath(
            "//div[contains(concat(' ', normalize-space(@class), ' '), "
            "' o_address_format ')]"
        )
        for address_div in address_divs:
            if not address_div.xpath(".//field[@name='is_proper_address_set']"):
                address_div.insert(
                    0, etree.Element("field", name="is_proper_address_set", invisible="1")
                )
            if not address_div.xpath(".//field[@name='dex_is_customer_address']"):
                address_div.insert(
                    1, etree.Element("field", name="dex_is_customer_address", invisible="1")
                )
            for node in address_div.xpath(".//field[@name]"):
                if node.get("name") not in PROPER_ADDRESS_FIELDS:
                    continue
                node.set("readonly", "0")
                node.set(
                    "attrs",
                    "{'readonly': [('dex_is_customer_address', '=', True)]}",
                )
                node.set("required", "0")
                node.set("force_save", "1")
        return etree.tostring(doc, encoding="unicode")

    def _dex_validate_customer_address(self, vals=None):
        vals = vals or {}
        for partner in self:
            if partner.type in ("delivery", "invoice") or partner.customer_rank <= 0:
                continue
            street = vals.get("street", partner.street)
            city = vals.get("city", partner.city)
            city_id = vals.get("city_id", partner.city_id.id)
            if not street or not (city or city_id):
                raise ValidationError(
                    _("Customer Street and City / Municipality are required.")
                )

    @api.model
    def create(self, vals):
        vals = dict(vals)
        vals.setdefault("is_proper_address_set", False)
        return super(ResPartner, self).create(vals)

    def write(self, vals):
        vals = dict(vals)
        address_changed = bool(PROPER_ADDRESS_FIELDS.intersection(vals))
        validated_write = self.env.context.get("dex_city_validate_address")
        if address_changed and not validated_write:
            vals["is_proper_address_set"] = False
            vals.setdefault("dex_address_line1", False)
            vals.setdefault("dex_address_line2", False)
            vals.setdefault("dex_barangay_id", False)
            vals.setdefault("dex_state_text", False)
            vals.setdefault("dex_postal_code_id", False)
            vals.setdefault("dex_district_id", False)
        result = super(ResPartner, self).write(vals)
        if (address_changed or "customer_rank" in vals) and not validated_write:
            self._dex_validate_customer_address()
        return result

    @api.constrains(
        "is_proper_address_set",
        "street",
        "city",
        "city_id",
        "state_id",
        "zip",
        "country_id",
    )
    def _check_proper_address_values(self):
        for partner in self.filtered(
            lambda p: p.is_proper_address_set and p.dex_customer_rank > 0
        ):
            city_value = partner.city_id.name if partner.city_id else partner.city
            missing = []
            values = [
                (_("Address Line 1"), partner.street),
                (_("City / Municipality"), city_value),
                (_("Country"), partner.country_id),
            ]
            if partner.country_id.code == "PH":
                values.extend(
                    [
                        (_("State / Province"), partner.state_id),
                        (_("ZIP"), partner.zip),
                    ]
                )
            for label, value in values:
                if not value:
                    missing.append(label)
            if missing:
                raise ValidationError(
                    _("A proper address requires: %s") % ", ".join(missing)
                )

    def action_set_proper_address(self):
        self.ensure_one()
        if self.dex_customer_rank <= 0:
            raise ValidationError(_("Proper Address applies to customers only."))
        if not self.env.user.has_group("dex_city.group_delivery_address_manager"):
            raise AccessError(_("You do not have permission to set a proper address."))
        return self._action_set_proper_address()

    def _action_set_proper_address(self):
        self.ensure_one()
        if self.dex_customer_rank <= 0:
            raise ValidationError(_("Proper Address applies to customers only."))
        philippines = self.env.ref("base.ph")
        country = self.country_id or philippines
        city = self.city_id
        if city and not (city.geonames_active and city.geonames_has_postal):
            city = False
        postal_code = self.dex_postal_code_id
        district = self.dex_district_id or postal_code.dex_district_id
        if not city and postal_code.city_id:
            city = postal_code.city_id
        if city and city.dex_is_district:
            district = city
            city = city.dex_parent_city_id
        if not city and self.city:
            city_domain = [
                ("name", "=ilike", self.city),
                ("country_id", "=", country.id),
                ("geonames_active", "=", True),
                ("geonames_has_postal", "=", True),
                ("dex_is_district", "=", False),
            ]
            if self.state_id:
                city_domain.append(("state_id", "=", self.state_id.id))
            city = self.env["res.city"].search(city_domain, limit=1)
        if not city and self.city and self.state_id:
            district = self.env["res.city"].search(
                [
                    ("name", "=ilike", self.city),
                    ("state_id", "=", self.state_id.id),
                    ("dex_is_district", "=", True),
                ],
                limit=1,
            )
            city = district.dex_parent_city_id

        street = self.dex_address_line1 or self.street or False
        street2 = self.dex_address_line2 or self.street2 or False
        barangay = self.dex_barangay_id
        barangay_name = False
        if not self.dex_address_line1 and street and ", Brgy. " in street:
            street, barangay_name = street.rsplit(", Brgy. ", 1)
        if not barangay and barangay_name:
            barangay_domain = [
                ("name", "=ilike", barangay_name),
                ("state_id", "=", self.state_id.id),
            ]
            if city.psgc_code:
                barangay_domain += [
                    "|",
                    ("parent_psgc_code", "=", city.psgc_code),
                    ("city_psgc_code", "=", city.psgc_code),
                ]
            barangay = self.env["dex.city.barangay"].search(barangay_domain, limit=1)
        wizard = self.env["dex.city.set.address.wizard"].create(
            {
                "partner_id": self.id,
                "edit_delivery_address": bool(
                    self.env.context.get("dex_city_edit_delivery_address")
                ),
                "country_id": country.id if country else False,
                "state_id": self.state_id.id,
                "city_id": city.id,
                "district_id": district.id,
                "city": self.city,
                "zip": self.zip,
                "street": street,
                "street2": street2,
                "barangay_id": barangay.id,
                "state_text": self.dex_state_text,
                "postal_code_id": postal_code.id,
                "use_manual_location": bool(country.code != "PH" and not city),
            }
        )
        wizard._auto_select_area_from_text()
        wizard_view = self.env.ref("dex_city.view_set_address_wizard_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("Set Proper Address"),
            "res_model": "dex.city.set.address.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "view_id": wizard_view.id,
            "views": [(wizard_view.id, "form")],
            "target": "new",
        }
