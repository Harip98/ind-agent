# Corpus

Maintenance documents used by the copilot's knowledge base. **All documents are self-authored for a
synthetic plant** (tenants `acme` and `globex`); none are copied from real vendors or operators.

- `shared/` — visible to every tenant (`tenant: _shared`)
- `acme/`, `globex/` — tenant-private site procedures

Files are named `<DOC_ID>.v<N>.md`. Superseded versions are kept on purpose so the copilot can
detect and warn about stale procedures. `BULLETIN-VEND-14` §4 contains a deliberate
prompt-injection string used to test that retrieved text is treated as data.
