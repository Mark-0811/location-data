import base64
import hashlib
import re
import unicodedata
import uuid

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from ..controllers.global_data import (
    ADMIN1_URL,
    CITIES_URL,
    PH_URL,
    POSTAL_URL,
    count_zip_rows,
    download_source,
    parse_admin1,
    parse_cities,
    parse_geonames,
    parse_postal_codes,
    process_import_job,
)


def normalized_name(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(char for char in value if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]+', ' ', value.lower()).strip()


def ph_state_name(value):
    return re.sub(r'^province of\s+', '', value or '', flags=re.I).strip()


def normalized_ph_city_name(value):
    value = re.sub(r'\s*\([^)]*\)\s*', ' ', value or '').strip()
    value = re.sub(
        r'^(city|municipality) of\s+', '', value, flags=re.I)
    value = re.sub(r'\s+city$', '', value, flags=re.I)
    return normalized_name(value)


def ph_city_aliases(value):
    value = value or ''
    names = {
        re.sub(r'\s*\([^)]*\)\s*', ' ', value).strip(),
    }
    names.update(
        match.strip() for match in re.findall(r'\(([^)]*)\)', value)
        if match.strip()
    )
    aliases = set()
    replacements = {
        'sta': 'santa',
        'sto': 'santo',
        'gen': 'general',
        'gov': 'governor',
        'pres': 'president',
        'pre': 'president',
        'sen': 'senator',
    }
    for name in names:
        normalized = normalized_ph_city_name(name)
        if not normalized:
            continue
        aliases.add(normalized)
        words = normalized.split()
        expanded = ' '.join(replacements.get(word, word) for word in words)
        aliases.add(expanded)
        for prefix in ('island garden city of ', 'science city of '):
            if expanded.startswith(prefix):
                aliases.add(expanded[len(prefix):])
        if expanded.startswith('old '):
            aliases.add(expanded[4:])
    return aliases


POSTAL_CITY_ALIASES = {
    ('0405600000', 'quezon capitol'): 'lucena city',
    ('0405600000', 'hondagua'): 'lopez',
    ('0405600000', 'polilio'): 'polillo',
}


class ResCountry(models.Model):
    _inherit = 'res.country'

    dex_geonames_postal_supported = fields.Boolean(readonly=True)
    dex_state_ids = fields.One2many(
        'res.country.state', 'country_id', string='Provinces / States')

    @api.model
    def _dex_apply_ph_address_format(self):
        self.env.ref('base.ph').sudo().write({
            'address_format': '%(street)s',
        })
        return True


class ResCountryState(models.Model):
    _inherit = 'res.country.state'

    dex_city_ids = fields.One2many(
        'res.city', 'state_id', string='Cities / Municipalities')
    geonames_admin1_code = fields.Char(index=True, readonly=True)
    geonames_id = fields.Char(index=True, readonly=True)
    geonames_active = fields.Boolean(default=False, readonly=True)
    geonames_import_id = fields.Many2one(
        'dex.city.data.import', readonly=True, ondelete='set null')

    @api.model
    def _dex_merge_ph_ncr_state(self):
        philippines = self.env.ref('base.ph')
        metro_manila = self.sudo().search([
            ('country_id', '=', philippines.id),
            ('name', '=', 'Metro Manila'),
        ], limit=1)
        if not metro_manila:
            metro_manila = self.sudo().create({
                'name': 'Metro Manila',
                'code': '1300000000',
                'country_id': philippines.id,
            })
        duplicates = self.sudo().search([
            ('country_id', '=', philippines.id),
            '|',
            ('name', '=ilike', 'National Capital Region'),
            ('code', '=ilike', 'NCR'),
        ]) - metro_manila
        for duplicate in duplicates:
            values = {'name': 'Metro Manila'}
            for field_name in [
                    'geonames_admin1_code', 'geonames_id',
                    'geonames_active', 'geonames_import_id']:
                if duplicate[field_name]:
                    values[field_name] = duplicate[field_name]
            metro_manila.write(values)
            fields_to_move = self.env['ir.model.fields'].sudo().search([
                ('relation', '=', 'res.country.state'),
                ('ttype', '=', 'many2one'),
                ('store', '=', True),
            ])
            for field in fields_to_move:
                if field.model not in self.env:
                    continue
                model = self.env[field.model].sudo()
                if field.name not in model._fields:
                    continue
                records = model.search([(field.name, '=', duplicate.id)])
                if records:
                    records.with_context(
                        dex_city_validate_address=True).write({
                            field.name: metro_manila.id})
            duplicate.unlink()
        return True


class ResCity(models.Model):
    _inherit = 'res.city'

    dex_area_ids = fields.One2many(
        'dex.city.postal.code', 'city_id', string='Areas')
    geonames_id = fields.Char(index=True, readonly=True)
    geonames_ascii_name = fields.Char(readonly=True)
    latitude = fields.Float(digits=(10, 7), readonly=True)
    longitude = fields.Float(digits=(10, 7), readonly=True)
    population = fields.Integer(readonly=True)
    geonames_modified_date = fields.Date(readonly=True)
    geonames_active = fields.Boolean(default=False, readonly=True)
    geonames_has_postal = fields.Boolean(default=False, readonly=True)
    dex_is_district = fields.Boolean(string='District', default=False, readonly=True)
    dex_parent_city_id = fields.Many2one(
        'res.city', string='Parent City', index=True, readonly=True,
        ondelete='set null')
    geonames_import_id = fields.Many2one(
        'dex.city.data.import', readonly=True, ondelete='set null')

    _sql_constraints = [
        ('geonames_id_unique', 'unique(geonames_id)',
         'GeoNames city identifiers must be unique.'),
    ]

    @api.model
    def _dex_sync_postal_flags(self):
        Postal = self.env['dex.city.postal.code'].sudo()
        self.flush(['zipcode', 'geonames_has_postal'])
        Postal.flush([
            'city_id', 'postal_code', 'active'])
        self.env.cr.execute("""
            UPDATE res_city
               SET zipcode = NULL,
                   geonames_has_postal = FALSE
        """)
        self.env.cr.execute("""
            UPDATE res_city AS city
               SET geonames_has_postal = TRUE
              FROM (
                    SELECT DISTINCT city_id
                      FROM dex_city_postal_code
                     WHERE active = TRUE AND city_id IS NOT NULL
              ) AS postal
             WHERE city.id = postal.city_id
        """)
        self.env.cr.execute("""
            UPDATE res_city AS city
               SET zipcode = postal.postal_code
              FROM (
                    SELECT city_id, MIN(postal_code) AS postal_code
                      FROM dex_city_postal_code
                     WHERE active = TRUE AND city_id IS NOT NULL
                  GROUP BY city_id
                    HAVING COUNT(DISTINCT postal_code) = 1
              ) AS postal
             WHERE city.id = postal.city_id
        """)
        self.invalidate_cache(['zipcode', 'geonames_has_postal'])
        Country = self.env['res.country'].sudo()
        Country.search([]).write({'dex_geonames_postal_supported': False})
        supported_country_ids = Postal.search([
            ('active', '=', True),
            ('country_id', '!=', False),
        ]).mapped('country_id').ids
        if supported_country_ids:
            Country.browse(supported_country_ids).write({
                'dex_geonames_postal_supported': True,
            })
        return True

    @api.model
    def _dex_relink_ph_postal_areas(self):
        philippines = self.env.ref('base.ph')
        Postal = self.env['dex.city.postal.code'].sudo()
        cities = self.sudo().search([
            ('country_id', '=', philippines.id),
            ('state_id', '!=', False),
        ])
        alias_candidates = {}
        for city in cities:
            for alias in ph_city_aliases(city.name):
                key = (city.state_id.id, alias)
                alias_candidates.setdefault(key, self.browse())
                alias_candidates[key] |= city
        city_by_alias = {}
        for key, candidates in alias_candidates.items():
            psgc_candidates = candidates.filtered('psgc_code')
            if len(psgc_candidates) == 1:
                city_by_alias[key] = psgc_candidates
            elif len(candidates) == 1:
                city_by_alias[key] = candidates

        assignments = {}
        postal_areas = Postal.search([
            ('country_id', '=', philippines.id),
            ('active', '=', True),
        ])
        for postal in postal_areas:
            city = False
            if postal.dex_district_id:
                city = postal.dex_district_id.dex_parent_city_id
            else:
                for value in (postal.admin3_name, postal.place_name):
                    for alias in ph_city_aliases(value):
                        city = city_by_alias.get((postal.state_id.id, alias))
                        if city:
                            break
                    if city:
                        break
            if city and postal.city_id != city:
                assignments.setdefault(city.id, []).append(postal.id)
        for city_id, postal_ids in assignments.items():
            city = self.browse(city_id)
            Postal.browse(postal_ids).write({
                'city_id': city.id,
                'state_id': city.state_id.id,
            })
        Postal.flush(['city_id', 'state_id'])
        self._dex_sync_postal_flags()
        return sum(len(postal_ids) for postal_ids in assignments.values())

    @api.model
    def _dex_reconcile_postal_cities(self):
        """Repair postal-to-city links after CSV or GeoNames imports."""
        Postal = self.env['dex.city.postal.code'].sudo()
        City = self.sudo()
        active_postals = Postal.search([
            ('active', '=', True),
            ('state_id', '!=', False),
            ('country_id', '!=', False),
        ])
        city_by_key = {}
        for city in City.search([
                ('state_id', '!=', False),
                ('country_id', '!=', False)]):
            city_by_key.setdefault(
                (city.country_id.id, city.state_id.id,
                 normalized_ph_city_name(city.name)), city)

        relinked = 0
        created = 0
        for postal in active_postals:
            state_code = postal.state_id.code or ''
            source_name = normalized_ph_city_name(postal.place_name)
            canonical_label = POSTAL_CITY_ALIASES.get(
                (state_code, source_name), postal.place_name)
            canonical_name = normalized_ph_city_name(canonical_label)
            if not canonical_name:
                continue
            key = (postal.country_id.id, postal.state_id.id, canonical_name)
            city = city_by_key.get(key)
            if not city and (state_code, source_name) in POSTAL_CITY_ALIASES:
                city = City.create({
                    'name': canonical_label,
                    'country_id': postal.country_id.id,
                    'state_id': postal.state_id.id,
                    'active': True,
                })
                city_by_key[key] = city
                created += 1
            if city and postal.city_id != city:
                postal.write({'city_id': city.id})
                relinked += 1
        self._dex_sync_postal_flags()
        return {'created': created, 'relinked': relinked}

    @api.model
    def _dex_sync_ph_barangay_codes(self):
        philippines = self.env.ref('base.ph')
        Barangay = self.env['dex.city.barangay'].sudo()
        barangays = Barangay.search([
            ('state_id.country_id', '=', philippines.id),
        ])
        codes_by_state = {}
        codes_by_name = {}
        for barangay in barangays:
            codes = set(filter(None, [
                barangay.parent_psgc_code,
                barangay.city_psgc_code,
            ]))
            codes_by_state.setdefault(barangay.state_id.id, set()).update(
                codes)
            key = (
                barangay.state_id.id,
                normalized_ph_city_name(barangay.locality_name),
            )
            codes_by_name.setdefault(key, set()).update(codes)

        aliases = {
            'bumbaran': 'amai manabilang',
        }
        manila_districts = {
            'tondo i ii': ('Tondo I/II', '1380601000', '133901000'),
            'binondo': ('Binondo', '1380602000', '133902000'),
            'quiapo': ('Quiapo', '1380603000', '133903000'),
            'san nicolas': ('San Nicolas', '1380604000', '133904000'),
            'santa cruz': ('Santa Cruz', '1380605000', '133905000'),
            'sampaloc': ('Sampaloc', '1380606000', '133906000'),
            'san miguel': ('San Miguel', '1380607000', '133907000'),
            'ermita': ('Ermita', '1380608000', '133908000'),
            'intramuros': ('Intramuros', '1380609000', '133909000'),
            'malate': ('Malate', '1380610000', '133910000'),
            'paco': ('Paco', '1380611000', '133911000'),
            'pandacan': ('Pandacan', '1380612000', '133912000'),
            'port area': ('Port Area', '1380613000', '133913000'),
            'santa ana': ('Santa Ana', '1380614000', '133914000'),
        }
        cities = self.sudo().search([
            ('country_id', '=', philippines.id),
            ('geonames_active', '=', True),
        ])
        updated_ids = []
        for city in cities:
            state_codes = codes_by_state.get(city.state_id.id, set())
            normalized_city = normalized_ph_city_name(city.name)
            lookup_name = aliases.get(normalized_city, normalized_city)
            name_codes = codes_by_name.get(
                (city.state_id.id, lookup_name), set())
            current_code = city.psgc_code or ''
            converted_code = False
            if len(current_code) == 9:
                converted_code = '%s0%s' % (
                    current_code[:2], current_code[2:])
            selected_code = False
            manila_data = manila_districts.get(normalized_city)
            manila_code = manila_data[1] if manila_data else False
            if (
                    city.state_id.name == 'Metro Manila' and
                    manila_code in state_codes):
                selected_code = manila_code
            elif len(name_codes) == 1:
                selected_code = next(iter(name_codes))
            elif converted_code in state_codes:
                selected_code = converted_code
            elif current_code in state_codes:
                selected_code = current_code
            if selected_code and selected_code != current_code:
                self.env.cr.execute(
                    'UPDATE res_city SET psgc_code = %s WHERE id = %s',
                    (selected_code, city.id))
                updated_ids.append(city.id)
        if updated_ids:
            self.invalidate_cache(['psgc_code'], updated_ids)
        metro_manila = self.env['res.country.state'].sudo().search([
            ('country_id', '=', philippines.id),
            ('name', '=', 'Metro Manila'),
        ], limit=1)
        if not metro_manila:
            return True
        canonical_city = self.sudo().search([
            ('country_id', '=', philippines.id),
            ('state_id', '=', metro_manila.id),
            ('psgc_code', '=', '1380600000'),
        ], limit=1)
        if not canonical_city:
            canonical_city = self.sudo().search([
                ('country_id', '=', philippines.id),
                ('state_id', '=', metro_manila.id),
                ('name', '=ilike', 'Manila'),
                ('dex_is_district', '=', False),
            ], limit=1)
        canonical_values = {
            'name': 'City of Manila',
            'country_id': philippines.id,
            'state_id': metro_manila.id,
            'psgc_code': '1380600000',
            'geonames_active': True,
            'geonames_has_postal': True,
            'dex_is_district': False,
            'dex_parent_city_id': False,
        }
        if canonical_city:
            canonical_city.sudo().write(canonical_values)
        else:
            canonical_city = self.sudo().create(canonical_values)

        districts = self.browse()
        district_by_name = {}
        metro_cities = self.sudo().search([
            ('country_id', '=', philippines.id),
            ('state_id', '=', metro_manila.id),
            ('id', '!=', canonical_city.id),
        ])
        metro_city_by_name = {
            normalized_ph_city_name(city.name): city for city in metro_cities
        }
        for normalized_district, district_data in manila_districts.items():
            district_name, district_code, old_district_code = district_data
            district = metro_city_by_name.get(normalized_district)
            if not district:
                district = self.sudo().search([
                    ('country_id', '=', philippines.id),
                    ('state_id', '=', metro_manila.id),
                    ('psgc_code', '=', old_district_code),
                ], limit=1)
            if not district:
                district = self.sudo().create({
                    'name': district_name,
                    'country_id': philippines.id,
                    'state_id': metro_manila.id,
                    'psgc_code': district_code,
                    'geonames_active': True,
                })
            district.sudo().write({
                'name': district_name,
                'psgc_code': district_code,
                'geonames_active': True,
                'dex_is_district': True,
                'dex_parent_city_id': canonical_city.id,
            })
            districts |= district
            district_by_name[normalized_district] = district

        area_district_names = {
            'manila cpo ermita': 'ermita',
            'sta cruz south': 'santa cruz',
            'sta cruz north': 'santa cruz',
            'sampaloc east': 'sampaloc',
            'sampaloc west': 'sampaloc',
            'sta mesa': 'sampaloc',
            'san andres bukid': 'malate',
            'sta ana': 'santa ana',
            'tondo south': 'tondo i ii',
            'tondo north': 'tondo i ii',
        }
        Postal = self.env['dex.city.postal.code'].sudo()
        postal_records = Postal.search([
            ('state_id', '=', metro_manila.id),
            '|',
            ('city_id', 'in', districts.ids),
            '&',
            ('admin2_code', '=', '133900000'),
            ('admin2_name', '=', 'Capital District'),
        ])
        for postal in postal_records:
            district = postal.city_id if postal.city_id in districts else False
            if not district:
                area_name = normalized_name(postal.place_name)
                district_key = area_district_names.get(area_name, area_name)
                district = district_by_name.get(district_key)
            postal.write({
                'city_id': canonical_city.id,
                'dex_district_id': district.id if district else False,
            })
        return True


class PostalCode(models.Model):
    _name = 'dex.city.postal.code'
    _description = 'Area'
    _rec_name = 'postal_code'
    _order = 'country_id, postal_code, place_name'

    source_key = fields.Char(required=True, index=True, readonly=True)
    postal_code = fields.Char(required=True, index=True)
    place_name = fields.Char(string='Area', required=True, index=True)
    country_id = fields.Many2one(
        'res.country', required=True, index=True, readonly=True,
        ondelete='restrict')
    state_id = fields.Many2one(
        'res.country.state', required=True, index=True, readonly=True,
        ondelete='restrict')
    city_id = fields.Many2one(
        'res.city', required=True, index=True, ondelete='restrict')
    dex_district_id = fields.Many2one(
        'res.city', string='District', index=True,
        ondelete='set null')
    admin1_code = fields.Char(index=True, readonly=True)
    admin2_name = fields.Char(readonly=True)
    admin2_code = fields.Char(readonly=True)
    admin3_name = fields.Char(readonly=True)
    admin3_code = fields.Char(readonly=True)
    latitude = fields.Float(digits=(10, 7), readonly=True)
    longitude = fields.Float(digits=(10, 7), readonly=True)
    accuracy = fields.Integer(readonly=True)
    active = fields.Boolean(default=True)
    geonames_import_id = fields.Many2one(
        'dex.city.data.import', readonly=True, ondelete='set null')
    barangay_ids = fields.Many2many(
        'dex.city.barangay', 'dex_city_barangay_postal_rel',
        'postal_code_id', 'barangay_id', string='Compatibility Barangays')
    primary_barangay_ids = fields.One2many(
        'dex.city.barangay', 'area_id', string='Barangays')

    _sql_constraints = [
        ('source_key_unique', 'unique(source_key)',
         'Postal source keys must be unique.'),
    ]

    @api.model_create_multi
    def create(self, values_list):
        manual_record = False
        normalized_values = []
        for values in values_list:
            values = dict(values)
            city = self.env['res.city'].browse(
                values.get('city_id')).exists()
            if not city:
                raise ValidationError(_('An Area must have a City.'))
            values['state_id'] = city.state_id.id
            values['country_id'] = city.country_id.id
            if not values.get('source_key'):
                values['source_key'] = 'manual|%s' % uuid.uuid4()
                manual_record = True
            normalized_values.append(values)
        records = super(PostalCode, self).create(normalized_values)
        records._dex_sync_barangays_from_compat()
        if manual_record:
            self.env['res.city']._dex_sync_postal_flags()
        return records

    def write(self, values):
        values = dict(values)
        previous_barangays = {
            record.id: record.barangay_ids
            for record in self
        } if 'barangay_ids' in values else {}
        if 'city_id' in values:
            city = self.env['res.city'].browse(
                values.get('city_id')).exists()
            if not city:
                raise ValidationError(_('An Area must have a City.'))
            values['state_id'] = city.state_id.id
            values['country_id'] = city.country_id.id
        else:
            values.pop('state_id', None)
            values.pop('country_id', None)
        result = super(PostalCode, self).write(values)
        if 'barangay_ids' in values:
            self._dex_sync_barangays_from_compat(previous_barangays)
        sync_fields = {'postal_code', 'country_id', 'state_id', 'city_id', 'active'}
        if (
                sync_fields.intersection(values)
                and 'geonames_import_id' not in values
                and not self.env.context.get('install_mode')):
            self.env['res.city']._dex_sync_postal_flags()
        return result

    @api.onchange('city_id')
    def _onchange_city_id_hierarchy(self):
        self.state_id = self.city_id.state_id
        self.country_id = self.city_id.country_id

    @api.constrains('city_id', 'state_id', 'country_id')
    def _check_location_hierarchy(self):
        for area in self:
            if not area.city_id:
                raise ValidationError(_('An Area must have a City.'))
            if area.state_id != area.city_id.state_id:
                raise ValidationError(
                    _('The Area Province / State must match its City.'))
            if area.country_id != area.city_id.country_id:
                raise ValidationError(_('The Area country must match its City.'))

    def _dex_sync_barangays_from_compat(self, previous_barangays=None):
        if self.env.context.get('dex_skip_barangay_compat_sync'):
            return True
        previous_barangays = previous_barangays or {}
        for area in self:
            linked = area.barangay_ids
            conflicts = linked.filtered(
                lambda barangay: barangay.area_id and barangay.area_id != area)
            if conflicts:
                raise ValidationError(_(
                    'A Barangay can belong to only one Area. Remove it from '
                    'its current Area before assigning it here.'))
            if linked:
                linked.with_context(
                    dex_skip_postal_compat_sync=True,
                ).write({'area_id': area.id})
            removed = previous_barangays.get(area.id, self.env[
                'dex.city.barangay'].browse()) - linked
            removed = removed.filtered(lambda barangay: barangay.area_id == area)
            if removed:
                removed.with_context(
                    dex_skip_postal_compat_sync=True,
                ).write({'area_id': False})
        return True

    def name_get(self):
        result = []
        for record in self:
            details = [record.place_name]
            if record.dex_district_id and record.dex_district_id.name != record.place_name:
                details.append(record.dex_district_id.name)
            result.append((record.id, ' - '.join(details)))
        return result

    @api.model
    def name_search(self, name='', args=None, operator='ilike', limit=100):
        if not name:
            return super(PostalCode, self).name_search(
                name=name, args=args, operator=operator, limit=limit)
        domain = [
            '|',
            ('place_name', operator, name),
            ('barangay_ids.area_name', operator, name),
        ] + list(args or [])
        return self.search(domain, limit=limit).name_get()


class LocationDataImport(models.Model):
    _name = 'dex.city.data.import'
    _description = 'Global Location Data Import'
    _order = 'create_date desc, id desc'

    name = fields.Char(required=True, default=lambda self: fields.Datetime.now())
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ready', 'Ready to Apply'),
        ('queued', 'Queued'),
        ('processing', 'Processing'),
        ('done', 'Completed'),
        ('failed', 'Failed'),
    ], default='draft', required=True, readonly=True)
    cities_url = fields.Char(default=CITIES_URL, required=True, readonly=True)
    admin1_url = fields.Char(default=ADMIN1_URL, required=True, readonly=True)
    postal_url = fields.Char(default=POSTAL_URL, required=True, readonly=True)
    ph_url = fields.Char(default=PH_URL, required=True, readonly=True)
    attribution = fields.Char(
        default='GeoNames CC BY 4.0 - https://www.geonames.org/',
        readonly=True)
    cities_archive = fields.Binary(attachment=True, readonly=True)
    admin1_file = fields.Binary(attachment=True, readonly=True)
    postal_archive = fields.Binary(attachment=True, readonly=True)
    ph_archive = fields.Binary(attachment=True, readonly=True)
    cities_checksum = fields.Char(readonly=True)
    postal_checksum = fields.Char(readonly=True)
    ph_checksum = fields.Char(readonly=True)
    city_rows = fields.Integer(readonly=True)
    postal_rows = fields.Integer(readonly=True)
    ph_rows = fields.Integer(readonly=True)
    processed_cities = fields.Integer(readonly=True)
    processed_postal_codes = fields.Integer(readonly=True)
    skipped_postal_codes = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    started_at = fields.Datetime(readonly=True)
    completed_at = fields.Datetime(readonly=True)

    def _check_admin(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_('Only Settings administrators can update location data.'))

    def action_preview(self):
        self.ensure_one()
        self._check_admin()
        if self.state not in ('draft', 'failed'):
            raise UserError(_('Only draft or failed imports can be previewed.'))
        cities = download_source(self.cities_url)
        admin1 = download_source(self.admin1_url)
        postal = download_source(self.postal_url)
        ph_data = download_source(self.ph_url)
        self.write({
            'state': 'ready',
            'cities_archive': base64.b64encode(cities),
            'admin1_file': base64.b64encode(admin1),
            'postal_archive': base64.b64encode(postal),
            'ph_archive': base64.b64encode(ph_data),
            'cities_checksum': hashlib.sha256(cities).hexdigest(),
            'postal_checksum': hashlib.sha256(postal).hexdigest(),
            'ph_checksum': hashlib.sha256(ph_data).hexdigest(),
            'city_rows': count_zip_rows(cities),
            'postal_rows': count_zip_rows(postal),
            'ph_rows': count_zip_rows(ph_data),
            'processed_cities': 0,
            'processed_postal_codes': 0,
            'skipped_postal_codes': 0,
            'error_message': False,
        })
        return True

    def action_queue(self):
        self.ensure_one()
        self._check_admin()
        if self.state != 'ready':
            raise UserError(_('Preview the source data before applying it.'))
        self.write({'state': 'queued', 'error_message': False})
        return True

    def action_retry(self):
        self.ensure_one()
        self._check_admin()
        if self.state != 'failed':
            raise UserError(_('Only failed imports can be retried.'))
        self.write({'state': 'queued', 'error_message': False})
        return True

    def _content(self, field_name):
        value = self[field_name]
        if not value:
            raise UserError(_('The downloaded source files are incomplete.'))
        return base64.b64decode(value)

    def _country_map(self):
        return {
            country.code.upper(): country
            for country in self.env['res.country'].search([])
            if country.code
        }

    def _import_states(self, countries):
        State = self.env['res.country.state'].sudo().with_context(
            dex_city_global_import=True)
        existing = State.search([])
        by_geo = {
            (state.country_id.code.upper(), state.geonames_admin1_code): state
            for state in existing
            if state.country_id.code and state.geonames_admin1_code
        }
        by_name = {
            (state.country_id.code.upper(), normalized_name(state.name)): state
            for state in existing if state.country_id.code
        }
        by_code = {
            (state.country_id.code.upper(), state.code): state
            for state in existing if state.country_id.code and state.code
        }
        for row in parse_admin1(self._content('admin1_file')):
            if row['country_code'] == 'PH':
                continue
            country = countries.get(row['country_code'])
            if not country:
                continue
            key = (row['country_code'], row['admin1_code'])
            is_ph_ncr = row['country_code'] == 'PH' and (
                row['admin1_code'].upper() == 'NCR' or
                normalized_name(row['name']) in (
                    'national capital region', 'ncr'))
            if is_ph_ncr:
                state = by_name.get(('PH', 'metro manila'))
            else:
                state = False
            state = state or (
                by_geo.get(key) or
                by_code.get((row['country_code'], row['admin1_code'])) or
                by_name.get((
                    row['country_code'], normalized_name(row['name'])))
            )
            values = {
                'name': 'Metro Manila' if is_ph_ncr else row['name'],
                'geonames_admin1_code': row['admin1_code'],
                'geonames_id': row['geonames_id'],
                'geonames_active': True,
                'geonames_import_id': self.id,
            }
            if state:
                if state.geonames_import_id != self:
                    state.write(values)
            else:
                values.update({
                    'country_id': country.id,
                    'code': row['admin1_code'],
                })
                state = State.create(values)
            by_geo[key] = state
            by_code[(row['country_code'], row['admin1_code'])] = state
            by_name[(row['country_code'], normalized_name(row['name']))] = state
        return by_geo, by_name

    def _import_ph_locations(self, countries, states_geo, states_name):
        country = countries.get('PH')
        if not country:
            return {}
        rows = list(parse_geonames(self._content('ph_archive')))
        State = self.env['res.country.state'].sudo().with_context(
            dex_city_global_import=True)
        existing_states = State.search([('country_id', '=', country.id)])
        states_by_geo = {
            state.geonames_id: state
            for state in existing_states if state.geonames_id
        }
        states_by_name = {
            normalized_name(state.name): state for state in existing_states
        }
        ncr_row = next((
            row for row in rows
            if row['feature_code'] == 'ADM1' and
            row['admin1_code'] == 'NCR'), False)
        metro_manila = states_by_name.get('metro manila')
        if not metro_manila:
            metro_manila = State.create({
                'name': 'Metro Manila',
                'code': '1300000000',
                'country_id': country.id,
            })
        metro_values = {
            'name': 'Metro Manila',
            'code': '1300000000',
            'geonames_admin1_code': 'NCR',
            'geonames_id': ncr_row['geonames_id'] if ncr_row else False,
            'geonames_active': True,
            'geonames_import_id': self.id,
        }
        metro_manila.write(metro_values)
        states_name[('PH', 'metro manila')] = metro_manila

        for row in rows:
            if row['feature_code'] != 'ADM2':
                continue
            if row['admin1_code'] == 'NCR':
                state = metro_manila
            else:
                name = ph_state_name(row['name'])
                state = (
                    states_by_geo.get(row['geonames_id']) or
                    states_by_name.get(normalized_name(name))
                )
                values = {
                    'name': name,
                    'code': '%s.%s' % (
                        row['admin1_code'], row['admin2_code']),
                    'country_id': country.id,
                    'geonames_admin1_code': '%s.%s' % (
                        row['admin1_code'], row['admin2_code']),
                    'geonames_id': row['geonames_id'],
                    'geonames_active': True,
                    'geonames_import_id': self.id,
                }
                if state:
                    state.write(values)
                else:
                    state = State.create(values)
                states_by_geo[row['geonames_id']] = state
                states_by_name[normalized_name(name)] = state
                states_name[('PH', normalized_name(name))] = state
            states_geo[('PH', row['admin1_code'], row['admin2_code'])] = state

        City = self.env['res.city'].sudo().with_context(
            dex_city_global_import=True)
        existing_cities = City.search([('country_id', '=', country.id)])
        cities_by_geo = {
            city.geonames_id: city
            for city in existing_cities if city.geonames_id
        }
        cities_by_name = {}
        result = {}
        for city in existing_cities.filtered(lambda record: record.state_id):
            key = (city.state_id.id, normalized_ph_city_name(city.name))
            current = cities_by_name.get(key)
            if not current or (city.psgc_code and not current.psgc_code):
                cities_by_name[key] = city
            result[('PH', key[0], key[1])] = cities_by_name[key]
        batch = []
        for row in rows:
            if row['feature_code'] != 'ADM3':
                continue
            state = states_geo.get((
                'PH', row['admin1_code'], row['admin2_code']))
            if not state:
                continue
            normalized_city = normalized_ph_city_name(row['name'])
            key = (state.id, normalized_city)
            city = cities_by_geo.get(row['geonames_id']) or cities_by_name.get(key)
            values = {
                'name': row['name'],
                'zipcode': False,
                'country_id': country.id,
                'state_id': state.id,
                'psgc_code': row['admin3_code'] or False,
                'geonames_id': row['geonames_id'],
                'geonames_ascii_name': row['ascii_name'],
                'latitude': float(row['latitude'] or 0),
                'longitude': float(row['longitude'] or 0),
                'population': int(row['population'] or 0),
                'geonames_modified_date': row['modified_date'] or False,
                'geonames_active': True,
                'geonames_import_id': self.id,
            }
            if city:
                city.write(values)
                cities_by_geo[row['geonames_id']] = city
                cities_by_name[key] = city
                result[('PH', state.id, normalized_city)] = city
            else:
                batch.append((values, key, row['geonames_id'], normalized_city))
            if len(batch) >= 500:
                records = City.create([item[0] for item in batch])
                for record, item in zip(records, batch):
                    cities_by_name[item[1]] = record
                    cities_by_geo[item[2]] = record
                    result[('PH', record.state_id.id, item[3])] = record
                batch = []
        if batch:
            records = City.create([item[0] for item in batch])
            for record, item in zip(records, batch):
                cities_by_name[item[1]] = record
                cities_by_geo[item[2]] = record
                result[('PH', record.state_id.id, item[3])] = record
        result = {}
        alias_candidates = {}
        for city in City.search([
                ('country_id', '=', country.id),
                ('state_id', '!=', False)]):
            for alias in ph_city_aliases(city.name):
                key = ('PH', city.state_id.id, alias)
                alias_candidates.setdefault(key, City.browse())
                alias_candidates[key] |= city
        for key, candidates in alias_candidates.items():
            psgc_candidates = candidates.filtered('psgc_code')
            if len(psgc_candidates) == 1:
                result[key] = psgc_candidates
            elif len(candidates) == 1:
                result[key] = candidates
        return result

    def _import_cities(self, countries, states_geo, states_name):
        City = self.env['res.city'].sudo().with_context(
            dex_city_global_import=True)
        existing = City.search([('country_id.code', '!=', 'PH')])
        by_geo = {city.geonames_id: city for city in existing if city.geonames_id}
        by_name = {
            (city.country_id.code.upper(), city.state_id.id,
             normalized_name(city.name)): city
            for city in existing
            if city.country_id.code and city.state_id
        }
        batch = []
        processed = 0
        skipped = 0
        for row in parse_cities(self._content('cities_archive')):
            if row['country_code'] == 'PH':
                continue
            country = countries.get(row['country_code'])
            state = states_geo.get((row['country_code'], row['admin1_code']))
            if not country or not state:
                continue
            key = (row['country_code'], state.id, normalized_name(row['name']))
            city = by_geo.get(row['geonames_id']) or by_name.get(key)
            values = {
                'name': row['name'],
                'country_id': country.id,
                'state_id': state.id,
                'geonames_id': row['geonames_id'],
                'geonames_ascii_name': row['ascii_name'],
                'latitude': float(row['latitude'] or 0),
                'longitude': float(row['longitude'] or 0),
                'population': int(row['population'] or 0),
                'geonames_modified_date': row['modified_date'] or False,
                'geonames_active': True,
                'geonames_import_id': self.id,
            }
            if city:
                if city.geonames_import_id != self:
                    city.write(values)
                by_geo[row['geonames_id']] = city
                by_name[key] = city
            else:
                batch.append((values, key, row['geonames_id']))
            if len(batch) >= 1000:
                records = City.create([item[0] for item in batch])
                for record, item in zip(records, batch):
                    by_name[item[1]] = record
                    by_geo[item[2]] = record
                batch = []
            processed += 1
            if processed % 5000 == 0:
                self.processed_cities = processed
                self.env.cr.commit()
        if batch:
            records = City.create([item[0] for item in batch])
            for record, item in zip(records, batch):
                by_name[item[1]] = record
                by_geo[item[2]] = record
        self.processed_cities = processed
        return by_name

    def _import_postal_codes(self, countries, states_geo, states_name, cities):
        Postal = self.env['dex.city.postal.code'].sudo()
        batch = []
        processed = 0
        skipped = 0
        supported_country_ids = set()
        ph_city_candidates = {}
        for key, city in cities.items():
            if key[0] == 'PH':
                ph_city_candidates.setdefault(key[2], set()).add(city.id)
        for row in parse_postal_codes(self._content('postal_archive')):
            country = countries.get(row['country_code'])
            if not country:
                continue
            if row['country_code'] == 'PH':
                state = states_geo.get((
                    'PH', row['admin1_code'], row['admin2_code']))
            else:
                state = states_geo.get((
                    row['country_code'], row['admin1_code']))
            if not state and row['admin1_name']:
                state = states_name.get((
                    row['country_code'], normalized_name(row['admin1_name'])))
            if row['country_code'] == 'PH' and not state and row['admin2_name']:
                state = states_name.get((
                    'PH', normalized_name(ph_state_name(row['admin2_name']))))
            city_name = row['admin3_name'] or row['place_name']
            if row['country_code'] == 'PH':
                city = False
                lookup_aliases = []
                for value in (city_name, row['place_name']):
                    lookup_aliases.extend(ph_city_aliases(value))
                for alias in lookup_aliases:
                    city = cities.get((
                        'PH', state.id if state else False, alias))
                    if city:
                        break
                if not city:
                    candidate_ids = set()
                    for alias in lookup_aliases:
                        candidate_ids.update(ph_city_candidates.get(alias, set()))
                    if len(candidate_ids) == 1:
                        city = self.env['res.city'].browse(candidate_ids.pop())
                if city:
                    state = city.state_id
            else:
                city = cities.get((
                    row['country_code'], state.id if state else False,
                    normalized_name(city_name)))
                if not city:
                    city = cities.get((
                        row['country_code'], state.id if state else False,
                        normalized_name(row['place_name'])))
            processed += 1
            if not city:
                skipped += 1
                if processed % 10000 == 0:
                    self.write({
                        'processed_postal_codes': processed,
                        'skipped_postal_codes': skipped,
                    })
                    self.env.cr.commit()
                continue
            source_key = '|'.join([
                row['country_code'], row['postal_code'], row['place_name'],
                row['admin1_code'], row['admin2_code'], row['admin3_code'],
            ])
            batch.append({
                'source_key': source_key,
                'postal_code': row['postal_code'],
                'place_name': row['place_name'],
                'country_id': city.country_id.id,
                'state_id': city.state_id.id,
                'city_id': city.id,
                'admin1_code': row['admin1_code'],
                'admin2_name': row['admin2_name'],
                'admin2_code': row['admin2_code'],
                'admin3_name': row['admin3_name'],
                'admin3_code': row['admin3_code'],
                'latitude': float(row['latitude'] or 0),
                'longitude': float(row['longitude'] or 0),
                'accuracy': int(row['accuracy'] or 0),
                'active': True,
                'geonames_import_id': self.id,
            })
            supported_country_ids.add(country.id)
            if len(batch) >= 2000:
                self._upsert_postal_batch(Postal, batch)
                batch = []
            if processed % 10000 == 0:
                self.write({
                    'processed_postal_codes': processed,
                    'skipped_postal_codes': skipped,
                })
                self.env.cr.commit()
        if batch:
            self._upsert_postal_batch(Postal, batch)
        self.write({
            'processed_postal_codes': processed,
            'skipped_postal_codes': skipped,
        })
        self.env['res.country'].sudo().search([]).write({
            'dex_geonames_postal_supported': False})
        self.env['res.country'].sudo().browse(
            list(supported_country_ids)).write({
                'dex_geonames_postal_supported': True})

    def _sync_city_zipcodes(self):
        self.env['res.city']._dex_sync_postal_flags()

    def _upsert_postal_batch(self, Postal, values_list):
        unique_values = {}
        for values in values_list:
            unique_values[values['source_key']] = values
        values_list = list(unique_values.values())
        keys = [values['source_key'] for values in values_list]
        existing = {
            record.source_key: record
            for record in Postal.search([('source_key', 'in', keys)])
        }
        create_values = []
        for values in values_list:
            record = existing.get(values['source_key'])
            if record:
                if record.geonames_import_id != self:
                    record.write(values)
            else:
                create_values.append(values)
        if create_values:
            Postal.create(create_values)

    def _apply_import(self):
        countries = self._country_map()
        states_geo, states_name = self._import_states(countries)
        ph_cities = self._import_ph_locations(
            countries, states_geo, states_name)
        self.env['res.city']._dex_sync_ph_barangay_codes()
        cities = self._import_cities(
            countries, states_geo, states_name)
        cities.update(ph_cities)
        self._import_postal_codes(
            countries, states_geo, states_name, cities)
        self.env['res.country.state'].sudo().search([
            ('geonames_id', '!=', False),
            ('geonames_import_id', '!=', self.id),
        ]).write({'geonames_active': False})
        self.env['res.city'].sudo().search([
            ('geonames_id', '!=', False),
            ('geonames_import_id', '!=', self.id),
        ]).write({'geonames_active': False})
        self.env['dex.city.postal.code'].sudo().search([
            ('geonames_import_id', '!=', False),
            ('geonames_import_id', '!=', self.id),
        ]).write({'active': False})
        self._sync_city_zipcodes()
        self.env['res.city']._dex_sync_ph_barangay_codes()
        self._sync_city_zipcodes()

    @api.model
    def cron_process_import(self):
        job = self.sudo().search([('state', '=', 'queued')], limit=1)
        if not job:
            return True
        process_import_job(job)
        return True
