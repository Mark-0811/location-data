from odoo import fields, models, _
from odoo.exceptions import ValidationError


class ProperAddressRequiredWizard(models.TransientModel):
    _name = "dex.city.proper.address.required.wizard"
    _description = "Set Proper Address Before Continuing"

    order_id = fields.Many2one("sale.order", required=True, readonly=True)
    operation = fields.Selection(
        [
            ("ready", "For Confirm"),
            ("print_open", "Print Quotation"),
            ("print_final", "Print Quotation"),
            ("set_indent", "Set NS Order"),
        ],
        required=True,
        readonly=True,
    )
    quotation_wizard_id = fields.Many2one(
        "dex_sale_order_print.quotation.wizard", readonly=True
    )
    invalid_partner_ids = fields.Many2many(
        "res.partner", string="Partners Requiring an Address", readonly=True
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Address to Set",
        domain="[('id', 'in', invalid_partner_ids)]",
    )
    invalid_address_summary = fields.Text(readonly=True)
    has_selectable_partner = fields.Boolean(readonly=True)

    def _refresh_invalid_addresses(self):
        self.ensure_one()
        entries = self.order_id._dex_invalid_proper_address_entries()
        partner_ids = [values["partner"].id for values in entries if values["partner"]]
        lines = []
        for values in entries:
            roles = values["roles"]
            role_label = (
                _("Invoice and Shipping Address") if len(roles) == 2 else roles[0]
            )
            partner = values["partner"]
            partner_name = partner.display_name if partner else _("Not selected")
            lines.append("%s: %s" % (role_label, partner_name))
        selected_partner = (
            self.partner_id.id
            if self.partner_id.id in partner_ids
            else (partner_ids[0] if partner_ids else False)
        )
        self.write(
            {
                "invalid_partner_ids": [(6, 0, partner_ids)],
                "partner_id": selected_partner,
                "invalid_address_summary": "\n".join(lines),
                "has_selectable_partner": bool(partner_ids),
            }
        )
        return bool(entries)

    def action_open(self):
        self.ensure_one()
        self._refresh_invalid_addresses()
        view = self.env.ref("dex_city.view_proper_address_required_wizard_form")
        return {
            "type": "ir.actions.act_window",
            "name": _("Set Proper Address before continuing"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": view.id,
            "views": [(view.id, "form")],
            "target": "new",
        }

    def action_set_selected_address(self):
        self.ensure_one()
        if not self.partner_id:
            raise ValidationError(
                _("Select the invoice or shipping partner on the Sale Order first.")
            )
        action = self.partner_id._action_set_proper_address()
        action["context"] = dict(
            self.env.context,
            dex_city_return_required_wizard_id=self.id,
        )
        return action

    def action_after_address_set(self):
        self.ensure_one()
        if self._refresh_invalid_addresses():
            return self.action_open()
        return self._continue_operation()

    def action_retry(self):
        self.ensure_one()
        if self._refresh_invalid_addresses():
            return self.action_open()
        return self._continue_operation()

    def _continue_operation(self):
        self.ensure_one()
        if self.operation == "ready":
            return self.order_id.action_ready()
        if self.operation == "print_open":
            return self.order_id.action_print_quotation()
        if self.operation == "print_final":
            quotation_wizard = self.quotation_wizard_id.exists()
            if not quotation_wizard:
                raise ValidationError(
                    _(
                        "The quotation print dialog has expired. Open Print Quotation again."
                    )
                )
            return quotation_wizard.action_quotation_print()
        if self.operation == "set_indent":
            return self.order_id.action_set_indent()
        raise ValidationError(_("The interrupted operation is not available."))
