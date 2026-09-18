# GFH Audit Automation

Modular rewrite of the GFH inventory count audit: the same variance pipeline
(store accounts, employees, excluded IMEIs — each importable from Excel with
a matching **Download Template** button) restructured into clean modules
(config, xlsx reader, models, database, pipeline, renderer, WhatsApp driver).

## Highlights
- SQLite/WAL database for runs and variances
- Image renderer for status reports + WhatsApp Web automation
- Same Import XLSX / Download Template workflow as the original audit app
