# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Bronze ingest
# MAGIC
# MAGIC 1. **Unzip** each new `export.zip` in the `landing` volume to a uniquely named
# MAGIC    `export_<timestamp>.xml` in the `raw` volume, then move the zip to `landing/_processed/`.
# MAGIC 2. **`COPY INTO`** three bronze Delta tables straight from those XML files, one per element
# MAGIC    type Apple writes: `<Record>`, `<Workout>` and `<ActivitySummary>`.
# MAGIC
# MAGIC `COPY INTO` remembers which files it has already loaded, so re-running the job is idempotent:
# MAGIC only newly landed exports are read. Bronze keeps every attribute as a string
# MAGIC (`inferSchema = false`), exactly as Apple wrote it, plus the source file and load time.
# MAGIC Typing, cleaning and de-duplication happen in silver.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "health")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")
fq = f"`{catalog}`.`{schema}`"

landing = f"/Volumes/{catalog}/{schema}/landing"
raw_xml = f"/Volumes/{catalog}/{schema}/raw/xml"

# COMMAND ----------

# MAGIC %md ## 1 · Unzip new exports

# COMMAND ----------

import os
import shutil
import zipfile
from datetime import datetime, timezone

os.makedirs(raw_xml, exist_ok=True)
os.makedirs(f"{landing}/_processed", exist_ok=True)

new_zips = sorted(f for f in os.listdir(landing) if f.lower().endswith(".zip"))
for name in new_zips:
    src = os.path.join(landing, name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    with zipfile.ZipFile(src) as z:
        member = next(m for m in z.namelist() if m.endswith("export.xml") and "cda" not in m.lower())
        target = os.path.join(raw_xml, f"export_{stamp}.xml")
        with z.open(member) as fin, open(target, "wb") as fout:
            shutil.copyfileobj(fin, fout, length=16 * 1024 * 1024)
    shutil.move(src, os.path.join(landing, "_processed", f"{stamp}_{name}"))
    print(f"{name} -> {target} ({os.path.getsize(target) / 1e6:.1f} MB)")

if not new_zips:
    print("No new exports in landing; COPY INTO will skip files it has already loaded.")

# COMMAND ----------

# MAGIC %md ## 2 · COPY INTO bronze
# MAGIC
# MAGIC Each table starts as a schemaless placeholder; `mergeSchema` lets the first `COPY INTO`
# MAGIC define the columns and lets later exports add attributes Apple introduces.
# MAGIC XML attributes land as `_attributeName` columns; nested children such as `MetadataEntry`
# MAGIC and `WorkoutStatistics` land as arrays of structs.

# COMMAND ----------

def copy_into(table: str, row_tag: str) -> None:
    spark.sql(f"CREATE TABLE IF NOT EXISTS {fq}.{table} COMMENT 'Bronze: raw Apple Health <{row_tag}> elements'")
    result = spark.sql(f"""
        COPY INTO {fq}.{table}
        FROM (
            SELECT *,
                   _metadata.file_name   AS _source_file,
                   current_timestamp()   AS _ingested_at
            FROM '{raw_xml}'
        )
        FILEFORMAT = XML
        PATTERN = 'export_*.xml'
        FORMAT_OPTIONS ('rowTag' = '{row_tag}', 'inferSchema' = 'false', 'valueTag' = '_text')
        COPY_OPTIONS ('mergeSchema' = 'true')
    """)
    stats = result.collect()[0].asDict()
    print(f"{table:<26} {stats}")


copy_into("bronze_records", "Record")
copy_into("bronze_workouts", "Workout")
copy_into("bronze_activity_summaries", "ActivitySummary")

# COMMAND ----------

display(spark.sql(f"""
    SELECT 'bronze_records' AS table_name, count(*) AS rows, count(DISTINCT _source_file) AS files FROM {fq}.bronze_records
    UNION ALL SELECT 'bronze_workouts', count(*), count(DISTINCT _source_file) FROM {fq}.bronze_workouts
    UNION ALL SELECT 'bronze_activity_summaries', count(*), count(DISTINCT _source_file) FROM {fq}.bronze_activity_summaries
"""))
