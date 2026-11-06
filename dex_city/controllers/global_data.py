import io
import urllib.request
import zipfile

from odoo import fields

CITIES_URL = "https://download.geonames.org/export/dump/cities500.zip"
ADMIN1_URL = "https://download.geonames.org/export/dump/admin1CodesASCII.txt"
POSTAL_URL = "https://download.geonames.org/export/zip/allCountries.zip"
PH_URL = "https://download.geonames.org/export/dump/PH.zip"


def download_source(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Dexterton-Odoo-dex_city/13.0",
        },
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return response.read()


def zip_text_lines(content):
    archive = zipfile.ZipFixle(io.BytesIO(content))
    filename = next(
        name
        for name in archive.namelist()
        if not name.endswith("/") and not name.lower().startswith("readme")
    )
    with archive.open(filename) as source:
        for raw_line in source:
            yield raw_line.decode("utf-8").rstrip("\r\n")


def count_zip_rows(content):
    return sum(1 for line in zip_text_lines(content) if line)


def parse_admin1(content):
    for raw_line in content.decode("utf-8").splitlines():
        values = raw_line.split("\t")
        if len(values) >= 4 and "." in values[0]:
            country_code, admin1_code = values[0].split(".", 1)
            yield {
                "country_code": country_code,
                "admin1_code": admin1_code,
                "name": values[1],
                "ascii_name": values[2],
                "geonames_id": values[3],
            }


def parse_cities(content):
    for row in parse_geonames(content):
        yield row


def parse_geonames(content):
    for line in zip_text_lines(content):
        values = line.split("\t")
        if len(values) >= 19:
            yield {
                "geonames_id": values[0],
                "name": values[1],
                "ascii_name": values[2],
                "latitude": values[4],
                "longitude": values[5],
                "feature_class": values[6],
                "feature_code": values[7],
                "country_code": values[8],
                "admin1_code": values[10],
                "admin2_code": values[11],
                "admin3_code": values[12],
                "admin4_code": values[13],
                "population": values[14] or "0",
                "modified_date": values[18],
            }


def parse_postal_codes(content):
    for line in zip_text_lines(content):
        values = line.split("\t")
        if len(values) >= 12:
            yield {
                "country_code": values[0],
                "postal_code": values[1],
                "place_name": values[2],
                "admin1_name": values[3],
                "admin1_code": values[4],
                "admin2_name": values[5],
                "admin2_code": values[6],
                "admin3_name": values[7],
                "admin3_code": values[8],
                "latitude": values[9],
                "longitude": values[10],
                "accuracy": values[11],
            }


def process_import_job(job, raise_on_error=False):
    job.ensure_one()
    job.write({
        "state": "processing",
        "started_at": fields.Datetime.now(),
        "error_message": False,
    })
    job.env.cr.commit()
    try:
        job._apply_import()
        job.write({
            "state": "done",
            "completed_at": fields.Datetime.now(),
        })
        job.env.cr.commit()
    except Exception as error:
        job.env.cr.rollback()
        job = job.sudo().browse(job.id).exists()
        if job:
            job.write({
                "state": "failed",
                "error_message": str(error),
            })
            job.env.cr.commit()
        if raise_on_error:
            raise
    return job


def download_location_data(env, name=None):
    Import = env["dex.city.data.import"].sudo()
    job = Import.create({
        "name": name or "GeoNames online refresh %s" % fields.Datetime.now(),
    })
    env.cr.commit()
    try:
        job.action_preview()
        job.action_queue()
        env.cr.commit()
    except Exception as error:
        env.cr.rollback()
        job = Import.browse(job.id).exists()
        if job:
            job.write({
                "state": "failed",
                "error_message": str(error),
            })
            env.cr.commit()
        raise
    return job


def put_location_data_in_models(env, job):
    job = env["dex.city.data.import"].sudo().browse(job.id).exists()
    if not job:
        raise ValueError("The location import job no longer exists.")
    if job.state == "ready":
        job.action_queue()
        env.cr.commit()
    if job.state != "queued":
        raise ValueError(
            "Location import job must be ready or queued, not %s." % job.state
        )
    return process_import_job(job, raise_on_error=True)


def import_location_data(env, name=None):
    job = download_location_data(env, name=name)
    return put_location_data_in_models(env, job)


def remove_location_data(env, include_philippines=False):
    State = env["res.country.state"].sudo()
    City = env["res.city"].sudo()
    Postal = env["dex.city.postal.code"].sudo()

    states = State.search([("geonames_id", "!=", False)])
    cities = City.search([("geonames_id", "!=", False)])
    postal_codes = Postal.search([])
    if not include_philippines:
        states = states.filtered(lambda record: record.country_id.code != "PH")
        cities = cities.filtered(
            lambda record: record.country_id.code != "PH"
        )
        postal_codes = postal_codes.filtered(
            lambda record: record.country_id.code != "PH"
        )

    states.write({"geonames_active": False})
    cities.write({"geonames_active": False, "geonames_has_postal": False})
    postal_codes.write({"active": False})
    if postal_codes:
        env["res.city"]._dex_sync_postal_flags()

    countries = postal_codes.mapped("country_id")
    if countries:
        countries.write({"dex_geonames_postal_supported": False})
    return {
        "states": len(states),
        "cities": len(cities),
        "postal_codes": len(postal_codes),
        "include_philippines": bool(include_philippines),
    }


def generate_all_data(env, name=None):
    job = import_location_data(
        env,
        name=name or "Manual complete location refresh %s" % fields.Datetime.now(),
    )
    return {
        "job_id": job.id,
        "state": job.state,
        "city_rows": job.city_rows,
        "postal_rows": job.postal_rows,
        "processed_cities": job.processed_cities,
        "processed_postal_codes": job.processed_postal_codes,
        "error_message": job.error_message or False,
    }
