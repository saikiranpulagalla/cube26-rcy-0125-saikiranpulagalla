-- Local-development roles only. Runtime code must use recovery_app, never the owner.
CREATE ROLE recovery_owner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
CREATE ROLE recovery_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
GRANT CONNECT ON DATABASE recovery TO recovery_owner, recovery_app;
GRANT USAGE ON SCHEMA public TO recovery_owner, recovery_app;
GRANT CREATE ON SCHEMA public TO recovery_owner;
