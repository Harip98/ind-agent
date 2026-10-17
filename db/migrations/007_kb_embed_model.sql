-- Re-embed a version when the embedding model changes, not only when its text changes.
ALTER TABLE kb.doc_versions ADD COLUMN embed_model text NOT NULL DEFAULT '';
