CREATE TABLE kb.documents (
    id         text PRIMARY KEY,
    tenant_id  text,                -- NULL = shared with all tenants
    title      text NOT NULL,
    doc_type   text NOT NULL
);

CREATE TABLE kb.doc_versions (
    doc_id          text NOT NULL REFERENCES kb.documents (id),
    version         integer NOT NULL,
    tenant_id       text,
    effective_date  date NOT NULL,
    superseded_by   integer,
    is_current      boolean NOT NULL,
    source_path     text NOT NULL,
    sha256          text NOT NULL,
    license         text NOT NULL,
    PRIMARY KEY (doc_id, version)
);

CREATE TABLE kb.sections (
    id          text PRIMARY KEY,   -- e.g. SOP-PUMP-07@v3#4.2
    doc_id      text NOT NULL,
    version     integer NOT NULL,
    tenant_id   text,
    section_no  text NOT NULL,
    heading     text NOT NULL,
    text        text NOT NULL,
    FOREIGN KEY (doc_id, version) REFERENCES kb.doc_versions (doc_id, version)
);

CREATE TABLE kb.chunks (
    id            text PRIMARY KEY,
    section_id    text NOT NULL REFERENCES kb.sections (id),
    tenant_id     text,
    asset_models  text[] NOT NULL,
    is_current    boolean NOT NULL,
    content       text NOT NULL,
    embedding     vector(768),
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX chunks_embedding_hnsw ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin ON kb.chunks USING gin (tsv);
CREATE INDEX chunks_filter ON kb.chunks (tenant_id, is_current);

GRANT SELECT ON ALL TABLES IN SCHEMA kb TO kb_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kb TO kb_writer;
