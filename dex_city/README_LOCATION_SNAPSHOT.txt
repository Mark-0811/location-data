DEX_CITY CURRENT LOCATION SNAPSHOT

Source database: dc20260727
Snapshot date: 6 October 2026 (UTC timestamp is in data/snapshot.json)
Package version: 13.0.2.2.43

The installation CSVs contain the current Countries, Provinces/States, Cities,
Districts, Areas, and Barangays, including manual records and inactive records.
City/District parents and Barangay City/Area links use external IDs, never raw
database IDs. The legacy Barangay/Area compatibility links are rebuilt by the
model methods when the canonical Barangay Area is loaded.

Load order: Countries -> Provinces/States -> Cities/Districts -> Areas -> Barangays.
The old installation hierarchy backfill is no longer called: the snapshot
already supplies the actual current links, including deliberately unassigned
Barangays. Historical migration code remains in the module.

Use this COMPLETE dex_city directory when reinstalling. Do not mix these CSVs
with an older dex_city version: the snapshot needs the deployed hierarchy fields.
The normal dependent Dexterton modules must also be available.
Do not uninstall the module on a production database merely to refresh data.
An uninstall can affect records referenced by addresses; take a full database
and filestore backup before any reinstall or other destructive maintenance.

This is a point-in-time location snapshot, not a backup of customers, addresses,
transactions, attachments, or the whole database. Changes made after the export
are not included. Source import jobs/archives were absent in the source database.

scripts/export_location_snapshot.py is a reusable Odoo-shell exporter. It stages
CSVs at LOCATION_SNAPSHOT_DIR, which must not already exist. It validates them
using rollback-only replay, then registers missing external IDs to avoid creating
duplicate records if the CSVs are loaded on the same database. It does not update
location business data. If import ownership records exist in a future database,
the exporter stops so their metadata can be handled explicitly.
After a new export, validate the files and deploy the staged CSVs deliberately;
the exporter does not automatically overwrite the module data directory.
