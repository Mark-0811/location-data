import logging
import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

CALOOCAN_AREA_NAMES = {
    "1": "Sangandaan",
    "8": "Dagat-Dagatan",
    "9": "Poblacion",
    "12": "Dagat-Dagatan",
    "13": "Poblacion",
    "14": "Dagat-Dagatan",
    "15": "Poblacion",
    "20": "Kaunlaran Village (C-3 Road)",
    "25": "Maypajo",
    "28": "Dagat-Dagatan",
    "29": "Maypajo",
    "36": "Marulas",
    "38": "Grace Park West",
    "43": "Grace Park West (3rd Avenue)",
    "46": "Grace Park West (4th Avenue)",
    "49": "Grace Park West (5th Avenue)",
    "52": "Grace Park West (7th Avenue)",
    "55": "Grace Park West (6th Avenue)",
    "56": "Grace Park West (8th Avenue)",
    "59": "Grace Park West (8th Avenue)",
    "62": "Grace Park West (10th Avenue)",
    "69": "Grace Park West",
    "71": "Grace Park West",
    "72": "Grace Park West (Victory Compound)",
    "73": "Grace Park West (PNR Compound)",
    "74": "Grace Park West (Heroes Del 96)",
    "76": "Grace Park West (Monumento)",
    "77": "University Hills (Samson Road)",
    "78": "University Hills (Monumento)",
    "79": "University Hills (Caimito Road)",
    "80": "University Hills (UE Caloocan)",
    "81": "Morning Breeze Subdivision",
    "84": "Morning Breeze Subdivision (MCU)",
    "85": "Morning Breeze Subdivision",
    "86": "Grace Park East (EDSA)",
    "88": "Grace Park East (11th Avenue)",
    "92": "Grace Park East, Madre Ignacia",
    "93": "Grace Park East",
    "94": "Grace Park East (Biglang-Awa)",
    "95": "Balintawak",
    "96": "Grace Park East (EDSA)",
    "97": "Grace Park East (11th Avenue)",
    "99": "Balintawak (Dorotea Compound)",
    "100": "Grace Park East (11th Avenue)",
    "101": "Grace Park East (Barrio Galino)",
    "102": "Grace Park East (9th Avenue)",
    "103": "Grace Park East (8th Avenue)",
    "106": "Grace Park East (10th Avenue)",
    "109": "Grace Park East (8th Avenue)",
    "110": "Grace Park East (7th Avenue)",
    "113": "Grace Park East (6th Avenue)",
    "117": "Grace Park East (5th Avenue)",
    "118": "Grace Park East (La Loma Cemetery)",
    "119": "Grace Park East (4th Avenue)",
    "120": "Grace Park East (2nd Avenue)",
    "121": "Grace Park East (7th Avenue)",
    "123": "Grace Park East (5th Avenue)",
    "126": "Barrio San Jose",
    "132": "Bagong Barrio West",
    "142": "Bagong Barrio West",
    "156": "Bagong Barrio East",
    "158": "Libis Baesa (Eternal Gardens Memorial Park)",
    "159": "Libis Baesa",
    "161": "Libis Reparo",
    "162": "Santa Quiteria",
    "164": "Talipapa",
    "165": "Bagbaguin",
    "166": "Kaybiga",
    "167": "Llano",
    "168": "Deparo",
    "169": "BF Homes Caloocan",
    "170": "Deparo 2",
    "171": "Bagumbong",
    "172": "Urduja Village",
    "173": "Congress",
    "174": "Camarin (Central)",
    "175": "Camarin",
    "176-A": "Bagong Silang",
    "176-B": "Bagong Silang",
    "176-C": "Bagong Silang",
    "176-D": "Bagong Silang",
    "176-E": "Bagong Silang",
    "176-F": "Bagong Silang",
    "177": "Camarin (Cielito)",
    "178": "Camarin (Kiko)",
    "179": "Amparo",
    "180": "Tala",
    "181": "Pangarap Village, Tala",
    "183": "Tala",
}


CALOOCAN_POSTAL_BARANGAYS = {
    ("1400", "cpo"): [
        "8",
        "9",
        "12",
        "13",
        "14",
        "15",
        "28",
        "36",
        "81",
        "84",
        "85",
        "132",
        "142",
        "156",
    ],
    ("1401", "baesa"): ["158", "159", "164"],
    ("1402", "quiteria"): ["162"],
    ("1403", "grace park east"): [
        "86",
        "88",
        "92",
        "93",
        "94",
        "95",
        "96",
        "97",
        "99",
        "100",
        "101",
        "102",
        "103",
        "106",
        "109",
        "110",
        "113",
        "117",
        "118",
        "119",
        "120",
        "121",
        "123",
    ],
    ("1404", "san jose"): ["126"],
    ("1405", "1st ave"): ["43", "46", "49", "52", "55"],
    ("1406", "grace park west"): [
        "38",
        "56",
        "59",
        "62",
        "69",
        "71",
        "72",
        "73",
        "74",
        "76",
    ],
    ("1407", "university hills"): ["77", "78", "79", "80"],
    ("1408", "sangandaan"): ["1"],
    ("1409", "kaunlaran"): ["20"],
    ("1410", "maypajo"): ["25", "29"],
    ("1420", "kaybiga"): ["165", "166", "168", "170"],
    ("1421", "bagumbong"): ["171", "172"],
    ("1422", "novaliches north"): ["169", "173", "174", "175", "177", "178"],
    ("1425", "amparo"): ["179"],
    ("1427", "tala"): ["180", "181", "183"],
    ("1428", "bagong silang"): ["176-A", "176-B", "176-C", "176-D", "176-E", "176-F"],
}


CALOOCAN_MISSING_POSTAL_AREAS = {
    "1409": "Kaunlaran Village",
    "1411": "Fish Market",
    "1413": "Kapitbahayan East",
}


def normalized_location_name(value):
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    replacements = {
        "gen": "general",
        "sta": "santa",
        "sto": "santo",
        "polo": "pulo",
    }
    return " ".join(replacements.get(word, word) for word in normalized.split())


def barangay_matches_area(barangay_name, area_name):
    barangay = normalized_location_name(barangay_name)
    area = normalized_location_name(area_name)
    if not barangay or not area:
        return False
    if barangay == area:
        return True
    barangay_tokens = set(barangay.split())
    area_tokens = set(area.split())
    return len(barangay_tokens) >= 1 and barangay_tokens.issubset(area_tokens)


class Barangay(models.Model):
    _name = "dex.city.barangay"
    _description = "PSGC Barangay"
    _order = "name, psgc_code"

    name = fields.Char(required=True, index=True)
    psgc_code = fields.Char(string="PSGC Code", index=True)
    parent_psgc_code = fields.Char(
        string="Immediate Parent PSGC Code", index=True
    )
    city_psgc_code = fields.Char(
        string="City / Municipality PSGC Code", index=True
    )
    locality_name = fields.Char(
        string="Source City / Municipality", index=True
    )
    area_name = fields.Char(string="Source Area Name", index=True)
    postal_code_ids = fields.Many2many(
        "dex.city.postal.code",
        "dex_city_barangay_postal_rel",
        "barangay_id",
        "postal_code_id",
        string="Postal Areas",
    )
    area_id = fields.Many2one(
        "dex.city.postal.code",
        string="Area",
        index=True,
        ondelete="set null",
    )
    city_id = fields.Many2one(
        "res.city",
        string="City / Municipality",
        index=True,
        ondelete="restrict",
    )
    state_id = fields.Many2one(
        "res.country.state",
        string="Province / State",
        required=True,
        ondelete="restrict",
        index=True,
    )
    country_id = fields.Many2one(
        "res.country",
        string="Country",
        readonly=True,
        index=True,
        ondelete="restrict",
    )

    _sql_constraints = [
        (
            "psgc_code_unique",
            "unique(psgc_code)",
            "The barangay PSGC code must be unique.",
        ),
    ]

    @api.model
    def _dex_compat_area_ids(self, commands, current_ids=None):
        area_ids = set(current_ids or [])
        for command in commands or []:
            if isinstance(command, int):
                area_ids.add(command)
                continue
            operation = command[0]
            if operation == 6:
                area_ids = set(command[2])
            elif operation == 5:
                area_ids.clear()
            elif operation == 4:
                area_ids.add(command[1])
            elif operation in (2, 3):
                area_ids.discard(command[1])
            elif operation == 0:
                raise ValidationError(_(
                    "Create Areas from the Areas menu before linking them "
                    "to a Barangay."))
        if len(area_ids) > 1:
            raise ValidationError(_("A Barangay can belong to only one Area."))
        return list(area_ids)

    @api.model
    def _dex_normalize_hierarchy_values(self, values, record=None, creating=False):
        values = dict(values)
        skip_compat = self.env.context.get("dex_skip_postal_compat_sync")
        current_area = record.area_id if record else self.env[
            "dex.city.postal.code"].browse()
        current_city = record.city_id if record else self.env["res.city"].browse()

        if "postal_code_ids" in values and not skip_compat and "area_id" not in values:
            area_ids = self._dex_compat_area_ids(
                values["postal_code_ids"],
                record.postal_code_ids.ids if record else [],
            )
            values["area_id"] = area_ids[0] if area_ids else False

        area = current_area
        if "area_id" in values:
            area = self.env["dex.city.postal.code"].browse(
                values.get("area_id")
            ).exists()
        city = current_city
        if "city_id" in values:
            city = self.env["res.city"].browse(values.get("city_id")).exists()

        if area:
            if city and "city_id" in values and city != area.city_id:
                raise ValidationError(_(
                    "The selected Area does not belong to the selected City."))
            city = area.city_id
            values["city_id"] = city.id
        elif (
                record and current_area and "area_id" not in values and
                "city_id" in values and city != current_area.city_id):
            raise ValidationError(_(
                "Clear the Area before changing the Barangay City."))

        if city:
            values["state_id"] = city.state_id.id
            values["country_id"] = city.country_id.id
        elif creating and not self.env.context.get("install_mode"):
            raise ValidationError(_(
                "A new Barangay must have a City or an Area."))
        elif "state_id" in values:
            state = self.env["res.country.state"].browse(
                values.get("state_id")
            ).exists()
            values["country_id"] = state.country_id.id if state else False
        else:
            values.pop("country_id", None)

        if "area_id" in values and not skip_compat:
            values["postal_code_ids"] = [
                (6, 0, [area.id] if area else [])
            ]
        return values

    @api.model_create_multi
    def create(self, values_list):
        normalized = [
            self._dex_normalize_hierarchy_values(values, creating=True)
            for values in values_list
        ]
        return super(Barangay, self).create(normalized)

    def write(self, values):
        if len(self) > 1 and (
                "postal_code_ids" in values or "area_id" in values or
                "city_id" in values):
            return all(record.write(values) for record in self)
        normalized = self._dex_normalize_hierarchy_values(
            values, record=self[:1], creating=False
        )
        return super(Barangay, self).write(normalized)

    @api.onchange("area_id")
    def _onchange_area_id_hierarchy(self):
        if self.area_id:
            self.city_id = self.area_id.city_id
            self.state_id = self.city_id.state_id
            self.country_id = self.city_id.country_id
        self.postal_code_ids = [(6, 0, self.area_id.ids)]

    @api.onchange("city_id")
    def _onchange_city_id_hierarchy(self):
        if self.area_id and self.area_id.city_id != self.city_id:
            self.area_id = False
            self.postal_code_ids = [(5, 0, 0)]
        if self.city_id:
            self.state_id = self.city_id.state_id
            self.country_id = self.city_id.country_id

    @api.constrains(
        "area_id", "city_id", "state_id", "country_id", "postal_code_ids"
    )
    def _check_location_hierarchy(self):
        for barangay in self:
            if len(barangay.postal_code_ids) > 1:
                raise ValidationError(_(
                    "A Barangay can belong to only one Area."))
            if barangay.area_id and barangay.postal_code_ids != barangay.area_id:
                raise ValidationError(_(
                    "The Barangay Area compatibility link is inconsistent."))
            if barangay.area_id and barangay.area_id.city_id != barangay.city_id:
                raise ValidationError(_(
                    "The Barangay Area and City must belong together."))
            if barangay.city_id:
                if barangay.state_id != barangay.city_id.state_id:
                    raise ValidationError(_(
                        "The Barangay Province / State must match its City."))
                if barangay.country_id != barangay.city_id.country_id:
                    raise ValidationError(_(
                        "The Barangay country must match its City."))

    @api.model
    def _dex_migrate_location_hierarchy(self):
        self.env.cr.execute("""
            UPDATE dex_city_barangay AS barangay
               SET area_id = links.postal_code_id
              FROM (
                    SELECT barangay_id, MIN(postal_code_id) AS postal_code_id
                      FROM dex_city_barangay_postal_rel
                  GROUP BY barangay_id
                    HAVING COUNT(DISTINCT postal_code_id) = 1
              ) AS links
             WHERE barangay.id = links.barangay_id
               AND barangay.area_id IS NULL
        """)
        migrated_areas = self.env.cr.rowcount
        self.env.cr.execute("""
            UPDATE dex_city_barangay AS barangay
               SET city_id = area.city_id
              FROM dex_city_postal_code AS area
             WHERE area.id = barangay.area_id
               AND area.city_id IS NOT NULL
               AND barangay.city_id IS DISTINCT FROM area.city_id
        """)
        self.env.cr.execute("""
            WITH exact_matches AS (
                SELECT barangay.id AS barangay_id,
                       MIN(city.id) AS city_id
                  FROM dex_city_barangay AS barangay
                  JOIN res_city AS city
                    ON city.psgc_code = barangay.city_psgc_code
                   AND COALESCE(city.dex_is_district, FALSE) = FALSE
                 WHERE barangay.city_id IS NULL
              GROUP BY barangay.id
                HAVING COUNT(city.id) = 1
            )
            UPDATE dex_city_barangay AS barangay
               SET city_id = matches.city_id
              FROM exact_matches AS matches
             WHERE barangay.id = matches.barangay_id
        """)
        migrated_cities = self.env.cr.rowcount
        self.env.cr.execute("""
            UPDATE dex_city_barangay AS barangay
               SET state_id = city.state_id,
                   country_id = city.country_id
              FROM res_city AS city
             WHERE city.id = barangay.city_id
               AND (barangay.state_id IS DISTINCT FROM city.state_id
                    OR barangay.country_id IS DISTINCT FROM city.country_id)
        """)
        self.env.cr.execute("""
            UPDATE dex_city_barangay AS barangay
               SET country_id = state.country_id
              FROM res_country_state AS state
             WHERE barangay.city_id IS NULL
               AND state.id = barangay.state_id
               AND barangay.country_id IS DISTINCT FROM state.country_id
        """)
        self.env.cr.execute("""
            DELETE FROM dex_city_barangay_postal_rel AS relation
             USING dex_city_barangay AS barangay
             WHERE relation.barangay_id = barangay.id
               AND (barangay.area_id IS NULL
                    OR relation.postal_code_id != barangay.area_id)
        """)
        self.env.cr.execute("""
            INSERT INTO dex_city_barangay_postal_rel
                        (barangay_id, postal_code_id)
                 SELECT barangay.id, barangay.area_id
                   FROM dex_city_barangay AS barangay
                  WHERE barangay.area_id IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1
                          FROM dex_city_barangay_postal_rel AS relation
                         WHERE relation.barangay_id = barangay.id
                           AND relation.postal_code_id = barangay.area_id)
        """)
        self.invalidate_cache([
            "area_id", "city_id", "state_id", "country_id",
            "postal_code_ids",
        ])
        unmatched = self.search_count([("city_id", "=", False)])
        _logger.info(
            "Location hierarchy migration: %s Area links, %s exact City "
            "matches, %s Barangays remain unmatched",
            migrated_areas, migrated_cities, unmatched,
        )
        return {
            "migrated_areas": migrated_areas,
            "migrated_cities": migrated_cities,
            "unmatched": unmatched,
        }

    @api.model
    def _dex_sync_caloocan_areas(self):
        philippines = self.env.ref("base.ph")
        city = (
            self.env["res.city"]
            .sudo()
            .search(
                [
                    ("country_id", "=", philippines.id),
                    ("psgc_code", "=", "1380100000"),
                    ("dex_is_district", "=", False),
                ],
                limit=1,
            )
        )
        if not city:
            return False
        Postal = self.env["dex.city.postal.code"].sudo()
        for postal_code, place_name in CALOOCAN_MISSING_POSTAL_AREAS.items():
            source_key = "dex_city|PH|Caloocan|%s|%s" % (
                postal_code,
                place_name,
            )
            postal = Postal.search([("source_key", "=", source_key)], limit=1)
            values = {
                "source_key": source_key,
                "postal_code": postal_code,
                "place_name": place_name,
                "country_id": philippines.id,
                "state_id": city.state_id.id,
                "city_id": city.id,
                "active": True,
            }
            if postal:
                postal.write(values)
            elif not Postal.search(
                [
                    ("city_id", "=", city.id),
                    ("postal_code", "=", postal_code),
                    ("place_name", "=ilike", place_name),
                ],
                limit=1,
            ):
                Postal.create(values)
        barangays = self.sudo().search(
            [
                ("state_id", "=", city.state_id.id),
                "|",
                ("parent_psgc_code", "=", city.psgc_code),
                ("city_psgc_code", "=", city.psgc_code),
            ]
        )
        by_number = {
            record.name.replace("Barangay ", "", 1): record for record in barangays
        }
        barangays.write({"area_name": False, "area_id": False})
        for number, area_name in CALOOCAN_AREA_NAMES.items():
            if by_number.get(number):
                by_number[number].area_name = area_name
        postal_areas = Postal.search([("city_id", "=", city.id), ("active", "=", True)])
        links = {}
        for (postal_code, name_part), numbers in CALOOCAN_POSTAL_BARANGAYS.items():
            postal = postal_areas.filtered(
                lambda area: area.postal_code == postal_code
                and name_part in normalized_location_name(area.place_name)
            )[:1]
            if not postal:
                continue
            for number in numbers:
                barangay = by_number.get(number)
                if barangay:
                    links[barangay.id] = postal.id
        for barangay_id, postal_id in links.items():
            self.browse(barangay_id).area_id = postal_id
        self.env["res.city"]._dex_sync_postal_flags()
        return True

    def name_get(self):
        area_name = self.env.context.get("dex_area_name")
        district_name = self.env.context.get("dex_district_name")
        city_id = self.env.context.get("dex_city_id")
        district_id = self.env.context.get("dex_district_id")
        postal_areas = self.env["dex.city.postal.code"].browse()
        if not area_name and city_id:
            domain = [
                ("city_id", "=", city_id),
                ("active", "=", True),
            ]
            if district_id:
                domain.append(("dex_district_id", "=", district_id))
            else:
                domain.append(("dex_district_id", "=", False))
            postal_areas = self.env["dex.city.postal.code"].search(
                domain, order="postal_code, place_name"
            )
        result = []
        for record in self:
            matching_names = []
            if area_name and barangay_matches_area(record.name, area_name):
                matching_names = [area_name]
            elif record.area_id:
                matching_names = record.area_id.mapped("place_name")
            elif record.postal_code_ids:
                matching_names = record.postal_code_ids.mapped("place_name")
            elif postal_areas:
                matching_names = postal_areas.filtered(
                    lambda area: barangay_matches_area(record.name, area.place_name)
                ).mapped("place_name")
            prefix = " / ".join(dict.fromkeys(matching_names))
            if not prefix:
                prefix = district_name or record.locality_name
            result.append((record.id, "%s - %s" % (prefix, record.name)))
        return result
