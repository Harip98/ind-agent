-- Make kb tenancy structural (M0 review): tenant_id is NOT NULL ('_shared' = all tenants),
-- part of every key, and carried down through composite foreign keys so a child row can never
-- carry a different tenant than its document. kb is still empty, so the tables are rebuilt.

DROP TABLE kb.chunks, kb.sections, kb.doc_versions, kb.documents;

CREATE TABLE kb.documents (
    tenant_id  text NOT NULL,      -- '_shared' = visible to every tenant
    id         text NOT NULL,
    title      text NOT NULL,
    doc_type   text NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE kb.doc_versions (
    tenant_id       text NOT NULL,
    doc_id          text NOT NULL,
    version         integer NOT NULL,
    effective_date  date NOT NULL,
    superseded_by   integer,
    is_current      boolean NOT NULL,
    source_path     text NOT NULL,
    sha256          text NOT NULL,
    license         text NOT NULL,
    PRIMARY KEY (tenant_id, doc_id, version),
    FOREIGN KEY (tenant_id, doc_id) REFERENCES kb.documents (tenant_id, id)
);

CREATE TABLE kb.sections (
    tenant_id   text NOT NULL,
    id          text NOT NULL,     -- e.g. SOP-PUMP-07@v3#4.2
    doc_id      text NOT NULL,
    version     integer NOT NULL,
    section_no  text NOT NULL,
    heading     text NOT NULL,
    text        text NOT NULL,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, doc_id, version) REFERENCES kb.doc_versions (tenant_id, doc_id, version)
);

CREATE TABLE kb.chunks (
    tenant_id     text NOT NULL,
    id            text NOT NULL,
    section_id    text NOT NULL,
    asset_models  text[] NOT NULL,
    is_current    boolean NOT NULL,
    content       text NOT NULL,
    embedding     vector(768),
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, section_id) REFERENCES kb.sections (tenant_id, id)
);
CREATE INDEX chunks_embedding_hnsw ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin ON kb.chunks USING gin (tsv);
CREATE INDEX chunks_filter ON kb.chunks (tenant_id, is_current);

GRANT SELECT ON ALL TABLES IN SCHEMA kb TO kb_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kb TO kb_writer;

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['documents', 'doc_versions', 'sections', 'chunks'] LOOP
        EXECUTE format('ALTER TABLE kb.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE kb.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_or_shared ON kb.%I '
            'USING (tenant_id = ''_shared'' OR tenant_id = public.current_tenant()) '
            'WITH CHECK (tenant_id = ''_shared'' OR tenant_id = public.current_tenant())', t);
    END LOOP;
END $$;
