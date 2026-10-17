-- Tenant of the current transaction; NULL when unset, so policies fail closed.
CREATE FUNCTION public.current_tenant() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.tenant_id', true), '') $$;

ALTER TABLE ops.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops.tenants
    USING (id = public.current_tenant())
    WITH CHECK (id = public.current_tenant());

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['assets', 'sensors', 'alarms', 'sensor_readings'] LOOP
        EXECUTE format('ALTER TABLE ops.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE ops.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON ops.%I '
            'USING (tenant_id = public.current_tenant()) '
            'WITH CHECK (tenant_id = public.current_tenant())', t);
    END LOOP;

    FOREACH t IN ARRAY ARRAY['documents', 'doc_versions', 'sections', 'chunks'] LOOP
        EXECUTE format('ALTER TABLE kb.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE kb.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_or_shared ON kb.%I '
            'USING (tenant_id IS NULL OR tenant_id = public.current_tenant()) '
            'WITH CHECK (tenant_id IS NULL OR tenant_id = public.current_tenant())', t);
    END LOOP;
END $$;
