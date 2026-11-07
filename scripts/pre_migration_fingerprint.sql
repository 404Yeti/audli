-- Read-only fingerprint of the `public` schema: object counts and a content hash per table. Used identically on the source (inside the dump's snapshot)
-- and on the restored copy, so the two outputs can be compared byte for byte. Row text is hashed in byte order (COLLATE "C", so the result does not depend on either server's collation) under UTC.
SET TIME ZONE 'UTC';
SET extra_float_digits = 3;
SELECT '## schema objects';
SELECT k || E'\t' || n FROM (
  SELECT 'tables' k, count(*) n FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('r', 'p')
  UNION ALL SELECT 'views', count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind IN ('v', 'm')
  UNION ALL SELECT 'sequences', count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relkind = 'S'
  UNION ALL SELECT 'columns', count(*) FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p') AND a.attnum > 0 AND NOT a.attisdropped
  UNION ALL SELECT 'indexes', count(*) FROM pg_indexes WHERE schemaname = 'public'
  UNION ALL SELECT 'constraints', count(*) FROM pg_constraint WHERE connamespace = 'public'::regnamespace
  UNION ALL SELECT 'triggers', count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid WHERE c.relnamespace = 'public'::regnamespace AND NOT t.tgisinternal
  UNION ALL SELECT 'policies', count(*) FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid WHERE c.relnamespace = 'public'::regnamespace
  UNION ALL SELECT 'functions', count(*) FROM pg_proc WHERE pronamespace = 'public'::regnamespace
  UNION ALL SELECT 'rls_enabled_tables', count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relrowsecurity
  UNION ALL SELECT 'table_grants', count(*) FROM pg_class c CROSS JOIN LATERAL aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) WHERE c.relnamespace = 'public'::regnamespace AND c.relkind IN ('r', 'p')
) s ORDER BY 1;
SELECT '## table row counts and content hashes';
SELECT t.table_name || E'\t' || (xpath('/row/n/text()', x.v))[1]::text || E'\t' || (xpath('/row/h/text()', x.v))[1]::text
FROM information_schema.tables t
CROSS JOIN LATERAL (SELECT query_to_xml(format('select count(*) as n, coalesce(md5(string_agg(r::text, chr(1) order by r::text collate "C")), ''empty'') as h from %I.%I r', t.table_schema, t.table_name), false, true, '') AS v) x
WHERE t.table_schema = 'public' AND t.table_type = 'BASE TABLE' ORDER BY t.table_name;
