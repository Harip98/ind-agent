-- Superseded sections point at the current section that replaces them (NULL = content removed).
ALTER TABLE kb.sections ADD COLUMN replaced_by text;
