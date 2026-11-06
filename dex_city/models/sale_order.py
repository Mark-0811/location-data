from odoo import api, fields, models, _
from odoo.exceptions import AccessError, ValidationError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    partner_customer_rank = fields.Integer(
        related="partner_id.dex_customer_rank", readonly=True
    )

    @api.depends(
        "partner_id",
        "partner_id.dex_customer_rank",
        "partner_id.child_ids",
        "partner_id.child_ids.type",
    )
    def _compute_delivery_address_ids(self):
        for order in self:
            order.delivery_address_ids = (
                order.partner_id.child_ids.filtered(
                    lambda partner: partner.type in ("delivery", "invoice")
                )
                if order.partner_id.dex_customer_rank > 0
                else self.env["res.partner"]
            )

    @api.depends(
        "partner_invoice_id",
        "partner_invoice_id.street",
        "partner_invoice_id.street2",
        "partner_invoice_id.city",
        "partner_invoice_id.city_id",
        "partner_invoice_id.state_id",
        "partner_invoice_id.zip",
        "partner_invoice_id.country_id",
        "partner_invoice_id.is_proper_address_set",
        "partner_invoice_id.dex_address_line1",
        "partner_invoice_id.dex_address_line2",
        "partner_invoice_id.dex_barangay_id",
        "partner_invoice_id.dex_state_text",
    )
    def _compute_invoice_address(self):
        for order in self:
            order.invoice_address = (
                ", ".join(order.partner_invoice_id._dex_address_lines())
                if order.partner_invoice_id
                else False
            )

    @api.depends(
        "partner_shipping_id",
        "partner_shipping_id.street",
        "partner_shipping_id.street2",
        "partner_shipping_id.city",
        "partner_shipping_id.city_id",
        "partner_shipping_id.state_id",
        "partner_shipping_id.zip",
        "partner_shipping_id.country_id",
        "partner_shipping_id.is_proper_address_set",
        "partner_shipping_id.dex_address_line1",
        "partner_shipping_id.dex_address_line2",
        "partner_shipping_id.dex_barangay_id",
        "partner_shipping_id.dex_state_text",
    )
    def _compute_delivery_address(self):
        for order in self:
            order.delivery_address = (
                ", ".join(order.partner_shipping_id._dex_address_lines())
                if order.partner_shipping_id
                else False
            )

    def _check_proper_address_partners(self):
        invalid_lines = []
        multiple_orders = len(self) > 1
        for order in self:
            for values in order._dex_invalid_proper_address_entries():
                roles = values["roles"]
                role_label = (
                    _("Invoice and Shipping Address") if len(roles) == 2 else roles[0]
                )
                partner = values["partner"]
                partner_name = partner.display_name if partner else _("Not selected")
                line = "%s: %s" % (role_label, partner_name)
                if multiple_orders:
                    line = "%s - %s" % (order.display_name, line)
                invalid_lines.append(line)
        if invalid_lines:
            raise ValidationError(
                _("Set Proper Address before continuing:\n%s")
                % "\n".join("- %s" % line for line in invalid_lines)
            )
        return True

    def _dex_partner_address_complete(self, partner):
        self.ensure_one()
        if not partner or not partner.country_id or not partner.street:
            return False
        if not (partner.city_id or partner.city):
            return False
        if partner.country_id.code == "PH":
            return bool(partner.state_id and partner.zip)
        return True

    def _dex_invalid_proper_address_entries(self):
        self.ensure_one()
        partners = {}
        for role, partner in [
            (_("Invoice Address"), self.partner_invoice_id),
            (_("Shipping Address"), self.partner_shipping_id),
        ]:
            if partner and partner.dex_customer_rank <= 0:
                continue
            if partner and (
                partner.is_proper_address_set
                or self._dex_partner_address_complete(partner)
            ):
                continue
            key = partner.id if partner else 0
            if key not in partners:
                partners[key] = {"partner": partner, "roles": []}
            partners[key]["roles"].append(role)
        return list(partners.values())

    def _dex_open_proper_address_required(self, operation, quotation_wizard=False):
        self.ensure_one()
        if not self._dex_invalid_proper_address_entries():
            return False
        wizard = self.env["dex.city.proper.address.required.wizard"].create(
            {
                "order_id": self.id,
                "operation": operation,
                "quotation_wizard_id": (
                    quotation_wizard.id if quotation_wizard else False
                ),
            }
        )
        return wizard.action_open()

    def action_ready(self):
        action = self._dex_open_proper_address_required("ready")
        if action:
            return action
        return super().action_ready()

    def action_print_quotation(self):
        action = self._dex_open_proper_address_required("print_open")
        if action:
            return action
        return super().action_print_quotation()

    def action_create_delivery_address(self):
        self.ensure_one()
        if not self.env.user.has_group("sales_team.group_sale_salesman"):
            raise AccessError(
                _("You do not have permission to create delivery addresses.")
            )
        if not self.partner_id:
            raise ValidationError(
                _("Select a customer before creating a delivery address.")
            )
        if self.partner_id.dex_customer_rank <= 0:
            raise ValidationError(_("Delivery addresses apply to customers only."))
        country = self.partner_id.country_id or self.env.ref("base.ph")
        wizard = self.env["dex.city.set.address.wizard"].create(
            {
                "sale_order_id": self.id,
                "partner_id": self.partner_id.id,
                "parent_partner_id": self.partner_id.id,
                "create_delivery_address": True,
                "show_address_form": False,
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
