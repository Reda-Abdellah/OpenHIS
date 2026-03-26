-- OpenELIS Liquibase migrations reference tables as clinlims.table_name,
-- so the clinlims schema must exist inside the clinlims database.
-- This script runs once when the postgres container first initialises.
\c clinlims
CREATE SCHEMA IF NOT EXISTS clinlims AUTHORIZATION clinlims;
