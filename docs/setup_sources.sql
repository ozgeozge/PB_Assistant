-- Seed supported external sources.
INSERT INTO "PB_Assistant_source" (name, display_name, description, base_url, enabled)
VALUES
    ('OpenAlexAPI', 'OpenAlex', NULL, NULL, TRUE),
    ('ScopusAPI', 'Scopus', NULL, NULL, TRUE),
    ('WoSAPI', 'Web of Science', NULL, NULL, TRUE)
ON CONFLICT (name) DO UPDATE
SET
    display_name = EXCLUDED.display_name,
    base_url = EXCLUDED.base_url,
    enabled = EXCLUDED.enabled;

-- Example BoundaryQuery rows for the Climate Change boundary.
-- Replace 'cc' and the query strings for other planetary boundaries.
INSERT INTO "PB_Assistant_boundaryquery" (planetary_boundary_id, source_id, query)
SELECT pb.id, src.id, q.query
FROM "PB_Assistant_planetaryboundary" pb
JOIN "PB_Assistant_source" src ON src.name = q.source_name
JOIN (
    VALUES
        ('OpenAlexAPI', '"climate change" AND "planetary boundaries" NOT "planetary boundary layer"'),
        ('ScopusAPI', 'TITLE-ABS-KEY("climate change" AND "planetary boundaries") AND NOT TITLE-ABS-KEY("planetary boundary layer")'),
        ('WoSAPI', 'TS=("climate change" AND "planetary boundaries") NOT TS=("planetary boundary layer")')
) AS q(source_name, query) ON TRUE
WHERE pb.short_name = 'cc'
ON CONFLICT (planetary_boundary_id, source_id) DO UPDATE
SET query = EXCLUDED.query;
