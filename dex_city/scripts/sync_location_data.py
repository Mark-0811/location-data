country_model = env['res.country'].sudo()
state_model = env['res.country.state'].sudo()
city_model = env['res.city'].sudo()
barangay_model = env['dex.city.barangay'].sudo()

country_model._dex_apply_ph_address_format()
state_model._dex_merge_ph_ncr_state()
city_model._dex_sync_ph_barangay_codes()
city_model._dex_relink_ph_postal_areas()
repair_result = city_model._dex_reconcile_postal_cities()
barangay_model._dex_sync_caloocan_areas()
env.cr.commit()

print('Location synchronization completed: %s' % repair_result)
