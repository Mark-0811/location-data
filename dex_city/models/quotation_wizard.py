from odoo import models


class QuotationChooseTemplate(models.TransientModel):
    _inherit = "dex_sale_order_print.quotation.wizard"

    def action_quotation_print(self):
        self.ensure_one()
        action = self.order_id._dex_open_proper_address_required(
            "print_final", quotation_wizard=self
        )
        if action:
            return action
        return super().action_quotation_print()
