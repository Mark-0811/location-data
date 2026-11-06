from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResCity(models.Model):
    _name = "res.city"
    _description = "City"
    _order = "name"

    name = fields.Char(required=True, index=True)
    zipcode = fields.Char(string="ZIP Code")
    state_id = fields.Many2one(
        "res.country.state",
        string="Province / State",
        required=True,
        ondelete="restrict",
    )
    country_id = fields.Many2one(
        "res.country", string="Country", readonly=True, ondelete="restrict"
    )
    active = fields.Boolean(default=True)
    psgc_code = fields.Char(string="PSGC Code", index=True)

    @api.model_create_multi
    def create(self, values_list):
        normalized_values = []
        State = self.env["res.country.state"]
        for values in values_list:
            values = dict(values)
            state = State.browse(values.get("state_id")).exists()
            if not state:
                raise ValidationError(_("A City must have a Province / State."))
            values["country_id"] = state.country_id.id
            normalized_values.append(values)
        return super(ResCity, self).create(normalized_values)

    def write(self, values):
        values = dict(values)
        if "state_id" in values:
            state = self.env["res.country.state"].browse(
                values.get("state_id")
            ).exists()
            if not state:
                raise ValidationError(_("A City must have a Province / State."))
            values["country_id"] = state.country_id.id
        elif "country_id" in values:
            values.pop("country_id")
        return super(ResCity, self).write(values)

    @api.onchange("state_id")
    def _onchange_state_id_hierarchy(self):
        self.country_id = self.state_id.country_id

    @api.constrains("state_id", "country_id")
    def _check_location_hierarchy(self):
        for city in self:
            if not city.state_id:
                raise ValidationError(_("A City must have a Province / State."))
            if city.country_id != city.state_id.country_id:
                raise ValidationError(
                    _("The City country must match its Province / State country.")
                )

    def name_get(self):
        result = []
        for city in self:
            details = []
            if city.state_id:
                details.append(city.state_id.name)
            if city.zipcode:
                details.append(city.zipcode)
            label = city.name
            if details:
                label = "%s (%s)" % (label, ", ".join(details))
            result.append((city.id, label))
        return result
