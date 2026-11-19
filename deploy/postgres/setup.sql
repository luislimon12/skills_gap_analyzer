-- Run once as the postgres superuser:  sudo -u postgres psql -f deploy/postgres/setup.sql
-- Then set a password:                 sudo -u postgres psql -c "\password skills_app"
-- The app role can create its own tables (Alembic) but has no superuser,
-- role-creation or database-creation rights.
CREATE ROLE skills_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
CREATE DATABASE skills_gap OWNER skills_app ENCODING 'UTF8';
REVOKE ALL ON DATABASE skills_gap FROM PUBLIC;
GRANT CONNECT ON DATABASE skills_gap TO skills_app;
\connect skills_gap
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO skills_app;
