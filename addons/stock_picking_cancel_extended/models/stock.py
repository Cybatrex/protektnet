# -*- coding: utf-8 -*-
# Part of BrowseInfo. See LICENSE file for full copyright and licensing details.

from odoo import api, models, _
from datetime import datetime
from odoo.tools import DEFAULT_SERVER_DATETIME_FORMAT
from odoo.tools.float_utils import float_round, float_is_zero

class Picking(models.Model):
    _inherit = 'stock.picking'

    @api.multi
    def action_cancel_draft(self):
        if not len(self.ids):
            return False
        move_obj = self.env['stock.move']
        for (ids, name) in self.name_get():
            message = _("Picking '%s' has been set in draft state.") % name
            self.message_post(message)
        for pick in self:
            ids2 = [move.id for move in pick.move_lines]
            moves = move_obj.browse(ids2)
            moves.sudo().action_draft()
        self.move_lines.mapped('move_line_ids').mapped(
            'lot_id').mapped('quant_ids').write({'lot_id': False})
        self.move_lines.mapped('move_line_ids').mapped('lot_id').unlink()
        self.move_lines.mapped('move_line_ids').write({
            'quantity_done': 0,
        })
        return True


class StockMove(models.Model):
    _inherit = 'stock.move'

    @api.multi
    def action_cancel_quant_create(self):
        quant_obj = self.env['stock.quant']
        for move in self:
            price_unit = move.get_price_unit()
            location = move.location_id
            rounding = move.product_id.uom_id.rounding
            vals = {
                'product_id': move.product_id.id,
                'location_id': location.id,
                'qty': float_round(move.product_uom_qty, precision_rounding=rounding),
                'cost': price_unit,
                'in_date': datetime.now().strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                'company_id': move.company_id.id,
            }
            quant_obj.sudo().create(vals)
            return

    @api.multi
    def action_draft(self):
        res = self.write({'state': 'draft'})
        return res

    def _do_unreserve(self):
        '''if any(move.state in ('done', 'cancel') for move in self):
            raise UserError(_('Cannot unreserve a done move'))'''
        for move in self:
            move.move_line_ids.unlink()
            if move.procure_method == 'make_to_order' and not move.move_orig_ids:
                move.state = 'waiting'
            elif move.move_orig_ids and not all(orig.state in ('done', 'cancel') for orig in move.move_orig_ids):
                move.state = 'waiting'
            else:
                move.state = 'confirmed'
        return True

    @api.multi
    def _action_cancel(self):
        '''if any(move.state == 'done' for move in self):
            raise UserError(_('You cannot cancel a stock move that has been set to \'Done\'.'))'''
        for move in self:

            move._do_unreserve()
            siblings_states = (move.move_dest_ids.mapped('move_orig_ids') - move).mapped('state')
            if move.propagate:
                # only cancel the next move if all my siblings are also cancelled
                if all(state == 'cancel' for state in siblings_states):
                    move.move_dest_ids._action_cancel()
            else:
                if all(state in ('done', 'cancel') for state in siblings_states):
                    move.move_dest_ids.write({'procure_method': 'make_to_stock'})
                    move.move_dest_ids.write({'move_orig_ids': [(3, move.id, 0)]})

            if move.picking_id.state == 'done' or 'confirmed':
                pack_op = self.env['stock.move'].sudo().search([('picking_id','=',move.picking_id.id),('product_id','=',move.product_id.id)])
                # outgoing
                for pack_op_id in pack_op: 
                    if move.picking_id.picking_type_id.code in ['outgoing','internal']:
                        if move.picking_id.sale_id.warehouse_id.delivery_steps == 'pick_ship':
                            if pack_op.location_dest_id.usage == 'customer':
                                outgoing_quant = self.env['stock.quant'].sudo().search([('product_id','=',move.product_id.id),('location_id','=',pack_op_id.location_dest_id.id)])
                                if outgoing_quant:
                                    old_qty = outgoing_quant[0].quantity
                                    outgoing_quant[0].quantity = old_qty - move.product_uom_qty
                            else:
                                outgoing_quant = self.env['stock.quant'].sudo().search([('product_id','=',move.product_id.id),('location_id','=',pack_op_id.location_id.id)])
                                if outgoing_quant:
                                    old_qty = outgoing_quant[0].quantity
                                    outgoing_quant[0].quantity = old_qty + move.product_uom_qty
                        else:
                            outgoing_quant = self.env['stock.quant'].sudo().search([('product_id','=',move.product_id.id),('location_id','=',pack_op_id.location_id.id)])
                            if outgoing_quant:
                                old_qty = outgoing_quant[0].quantity
                                outgoing_quant[0].quantity = old_qty + move.product_uom_qty

                    if move.picking_id.picking_type_id.code == 'incoming':
                        incoming_quant = self.env['stock.quant'].sudo().search([('product_id','=',move.product_id.id),('location_id','=',pack_op_id.location_dest_id.id)])
                        if incoming_quant:
                            old_qty = incoming_quant[0].quantity
                            incoming_quant[0].quantity = old_qty - move.product_uom_qty
            self.write({'state': 'cancel', 'move_orig_ids': [(5, 0, 0)]})

        return True

class stock_move_line(models.Model):
    _inherit = "stock.move.line"

    def unlink(self):
        precision = self.env['decimal.precision'].precision_get('Product Unit of Measure')
        for ml in self:
            '''if ml.state in ('done', 'cancel'):
                raise UserError(_('You can not delete product moves if the picking is done. You can only correct the done quantities.'))'''
            # Unlinking a move line should unreserve.
            if ml.product_id.type == 'product' and not ml.location_id.should_bypass_reservation() and not float_is_zero(ml.product_qty, precision_digits=precision):
                self.env['stock.quant']._update_reserved_quantity(
                    ml.product_id, ml.location_id, -ml.product_qty, lot_id=ml.lot_id,
                    package_id=ml.package_id, owner_id=ml.owner_id, strict=True)
        return True
