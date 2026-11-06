"""Run with Odoo 13 shell. Exports installation CSVs, never edits location rows.

Set LOCATION_SNAPSHOT_DIR to a new staging directory before running. Missing
external IDs are registered only after a successful rollback-only CSV replay.
"""
import csv
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from odoo.tools.convert import convert_csv_import

output = Path(os.environ['LOCATION_SNAPSHOT_DIR'])
output.mkdir(parents=True, exist_ok=False)
env.cr.rollback()
env.cr.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
specifications = [
    ('res.country', 'name code phone_code vat_label name_position address_format dex_geonames_postal_supported'),
    ('res.country.state', 'name code country_id geonames_admin1_code geonames_id geonames_active'),
    ('res.city', 'name zipcode state_id country_id active psgc_code geonames_id geonames_ascii_name latitude longitude population geonames_modified_date geonames_active geonames_has_postal dex_is_district dex_parent_city_id'),
    ('dex.city.postal.code', 'source_key postal_code place_name country_id state_id city_id dex_district_id admin1_code admin2_name admin2_code admin3_name admin3_code latitude longitude accuracy active'),
    ('dex.city.barangay', 'name psgc_code parent_psgc_code city_psgc_code locality_name area_name state_id country_id city_id area_id'),
]
models = {name: env[name].sudo().with_context(active_test=False, lang=None) for name, _ in specifications}
records = {name: model.search([], order='id') for name, model in models.items()}
rows = {name: recordset.read(fields.split()) for (name, fields), recordset in zip(specifications, records.values())}
for name, model in models.items():
    if 'geonames_import_id' in model._fields and model.search_count([('geonames_import_id', '!=', False)]):
        raise ValueError('Import ownership records must be exported before proceeding: ' + name)

# Reuse existing module/base XML IDs. Never put raw database IDs in references.
identifiers = {}
pending_ids = []
for name, recordset in records.items():
    identifiers[name] = recordset.get_external_id()
    for record_id in recordset.ids:
        if not identifiers[name].get(record_id):
            identifier = 'snapshot_%s_%s' % (name.replace('.', '_'), record_id)
            existing = env['ir.model.data'].search([('module', '=', 'dex_city'), ('name', '=', identifier)])
            if existing:
                raise ValueError('Snapshot external ID collision: ' + identifier)
            pending_ids.append({'module': 'dex_city', 'name': identifier, 'model': name, 'res_id': record_id, 'noupdate': False})
            identifiers[name][record_id] = 'dex_city.' + identifier
if pending_ids:
    env['ir.model.data'].sudo().create(pending_ids)

# Parent cities/districts must be defined before a row can reference them.
city_rows = {row['id']: row for row in rows['res.city']}
ordered_cities = []
visited = set()
visiting = set()
def visit_city(record_id):
    if record_id in visited:
        return
    if record_id in visiting:
        raise ValueError('Cyclic parent City relationship')
    visiting.add(record_id)
    parent = city_rows[record_id]['dex_parent_city_id']
    if parent:
        visit_city(parent[0])
    visiting.remove(record_id)
    visited.add(record_id)
    ordered_cities.append(city_rows[record_id])
for record_id in sorted(city_rows):
    visit_city(record_id)
rows['res.city'] = ordered_cities

def csv_value(model_name, field_name, value):
    field = models[model_name]._fields[field_name]
    if field.type == 'many2one':
        return identifiers[field.comodel_name][value[0]] if value else ''
    if field.type == 'boolean':
        return 'True' if value else 'False'
    if value is False or value is None:
        return ''
    return str(value)

files = []
for name, fields in specifications:
    field_names = fields.split()
    headers = ['id'] + [field + (':id' if models[name]._fields[field].type == 'many2one' else '') for field in field_names]
    path = output / (name + '.csv')
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(headers)
        for row in rows[name]:
            writer.writerow([identifiers[name][row['id']]] + [csv_value(name, field, row[field]) for field in field_names])
    files.append({'filename': path.name, 'model': name, 'records': len(rows[name]), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})

# Ensure the canonical and compatibility links really describe the same data.
barangays = records['dex.city.barangay']
for barangay in barangays:
    if set(barangay.postal_code_ids.ids) != set(barangay.area_id.ids):
        raise ValueError('Unsynchronized Barangay compatibility link: %s' % barangay.id)

# Exercise the real Odoo CSV loader against existing records; rollback all
# replay writes and model side effects. Only new XML IDs above are committed.
env.cr.execute('SAVEPOINT location_snapshot_replay')
try:
    for item in files:
        path = output / item['filename']
        convert_csv_import(env.cr, 'dex_city', str(path), path.read_bytes(), mode='init')
        print('CSV_REPLAY_OK', item['filename'], item['records'], flush=True)
finally:
    env.cr.execute('ROLLBACK TO SAVEPOINT location_snapshot_replay')
    env.cr.execute('RELEASE SAVEPOINT location_snapshot_replay')
    for model in models.values():
        model.invalidate_cache()

report = {
    'database': env.cr.dbname,
    'exported_at_utc': datetime.utcnow().isoformat() + 'Z',
    'source_module_version': env['ir.module.module'].search([('name', '=', 'dex_city')]).latest_version,
    'files': files,
    'registered_external_ids': len(pending_ids),
    'area_links': barangays.search_count([('area_id', '!=', False)]),
    'city_links': barangays.search_count([('city_id', '!=', False)]),
    'unassigned_cities': barangays.search_count([('city_id', '=', False)]),
    'manual_areas': models['dex.city.postal.code'].search_count([('source_key', '=like', 'manual|%')]),
    'csv_replay': 'passed (all location writes rolled back)',
    'ownership': 'No location records had geonames_import_id; import jobs and source archives are not included.',
}
(output / 'snapshot.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
env.cr.commit()
print('SNAPSHOT_COMPLETE ' + json.dumps(report, sort_keys=True), flush=True)
