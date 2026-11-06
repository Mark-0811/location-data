odoo.define('dex_city.create_contact_address', function (require) {
    'use strict';

    var core = require('web.core');
    var viewDialogs = require('web.view_dialogs');

    var _t = core._t;
    var addressFields = [
        'street', 'street2', 'city', 'city_id', 'state_id', 'zip', 'country_id',
    ];

    viewDialogs.FormViewDialog.include({
        init: function (parent, options) {
            this._super.apply(this, arguments);
            var context = options.context || {};
            if (options.res_model !== 'res.partner' || !context.default_is_subaddress) {
                return;
            }
            var self = this;
            this.buttons.splice(this.buttons.length - 1, 0, {
                text: _t('Set Address'),
                classes: 'btn-primary dex_city_set_child_address',
                click: function () {
                    return self._saveChildAndOpenAddress();
                },
            });
        },

        _allowEmptyChildAddress: function () {
            var renderer = this.form_view && this.form_view.renderer;
            if (!renderer) {
                return;
            }
            var widgetsByRecord = renderer.allFieldWidgets || {};
            Object.keys(widgetsByRecord).forEach(function (recordID) {
                widgetsByRecord[recordID].forEach(function (widget) {
                    if (addressFields.indexOf(widget.name) === -1) {
                        return;
                    }
                    widget.__node.attrs.modifiers =
                        widget.__node.attrs.modifiers || {};
                    widget.__node.attrs.modifiers.required = false;
                    var modifierData = renderer._getModifiersData(widget.__node);
                    if (modifierData) {
                        modifierData.modifiers.required = false;
                        Object.keys(modifierData.evaluatedModifiers).forEach(
                            function (id) {
                                modifierData.evaluatedModifiers[id].required = false;
                            }
                        );
                    }
                    widget.$el.removeClass('o_required_modifier o_field_invalid');
                    var labelID = renderer.idsForLabels[widget.name];
                    if (labelID) {
                        renderer.$('label[for="' + labelID + '"]')
                            .removeClass('o_field_invalid');
                    }
                });
            });
        },

        _save: function () {
            var context = this.options.context || {};
            if (this.res_model === 'res.partner' && context.default_is_subaddress) {
                this._allowEmptyChildAddress();
            }
            return this._super.apply(this, arguments);
        },

        _saveChildAndOpenAddress: function () {
            if (this._dexCityAddressPromise) {
                return this._dexCityAddressPromise;
            }
            var self = this;
            var parentNotified = false;
            this._allowEmptyChildAddress();
            this._dexCityAddressPromise = this.form_view.saveRecord(
                this.form_view.handle, {
                stayInEdit: true,
                reload: false,
                savePoint: false,
                viewType: 'form',
            }).then(function () {
                var record = self.form_view.model.get(self.form_view.handle);
                return self._rpc({
                    model: 'res.partner',
                    method: 'action_set_proper_address',
                    args: [[record.res_id]],
                    context: record.context,
                });
            }).then(function (action) {
                return self.do_action(action, {
                    on_close: function () {
                        if (
                            parentNotified || !self.form_view ||
                            self.isDestroyed()
                        ) {
                            return;
                        }
                        parentNotified = true;
                        return self.form_view.reload().then(function () {
                            var record = self.form_view.model.get(
                                self.form_view.handle
                            );
                            return Promise.resolve(
                                self.on_saved(record, true)
                            ).then(function () {
                                self.close();
                            });
                        });
                    },
                });
            });
            this._dexCityAddressPromise = this._dexCityAddressPromise.then(
                function (result) {
                    self._dexCityAddressPromise = null;
                    return result;
                },
                function (error) {
                    self._dexCityAddressPromise = null;
                    throw error;
                }
            );
            return this._dexCityAddressPromise;
        },
    });
});
