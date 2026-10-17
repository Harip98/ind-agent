DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['ops_reader', 'ops_loader', 'kb_reader', 'kb_writer'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS', r);
        END IF;
    END LOOP;
END $$;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS kb;
REVOKE ALL ON SCHEMA ops FROM PUBLIC;
REVOKE ALL ON SCHEMA kb FROM PUBLIC;
GRANT USAGE ON SCHEMA ops TO ops_reader, ops_loader;
GRANT USAGE ON SCHEMA kb TO kb_reader, kb_writer;
