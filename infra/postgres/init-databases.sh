#!/bin/bash
set -e
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-EOSQL
  SELECT 'CREATE DATABASE ehr' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='ehr')\gexec
  SELECT 'CREATE DATABASE hapi_fhir' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='hapi_fhir')\gexec
  GRANT ALL PRIVILEGES ON DATABASE ehr TO orthanc;
  GRANT ALL PRIVILEGES ON DATABASE hapi_fhir TO orthanc;

  SELECT 'CREATE DATABASE mpi' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname='mpi')\gexec
  DO \$\$
  BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'mpi') THEN
      CREATE USER mpi WITH PASSWORD 'mpi';
    END IF;
  END
  \$\$;
  GRANT ALL PRIVILEGES ON DATABASE mpi TO mpi;
EOSQL
