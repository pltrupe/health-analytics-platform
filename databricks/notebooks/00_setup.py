# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · Setup
# MAGIC
# MAGIC Creates the schema and the three Unity Catalog **Volumes** the pipeline uses:
# MAGIC
# MAGIC | Volume | Holds |
# MAGIC |---|---|
# MAGIC | `landing` | `export.zip` files exactly as the iPhone Health app produces them |
# MAGIC | `raw` | the extracted `export.xml` files that `COPY INTO` reads |
# MAGIC | `exports` | JSON snapshots of the gold tables for the React dashboard |
# MAGIC
# MAGIC Exports arrive by dropping `export.zip` into `landing` (Catalog Explorer upload, or
# MAGIC `databricks fs cp`; `scripts/run_pipeline.sh` does this with the synthetic sample).

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "health")

catalog = dbutils.widgets.get("catalog")
schema = dbutils.widgets.get("schema")

# COMMAND ----------

spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}` COMMENT 'Apple Health analytics: bronze, silver and gold layers'")
for volume, comment in [
    ("landing", "Apple Health export.zip drop zone"),
    ("raw", "Extracted export.xml files read by COPY INTO"),
    ("exports", "Gold-table JSON snapshots served by the dashboard"),
]:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS `{catalog}`.`{schema}`.`{volume}` COMMENT '{comment}'")
