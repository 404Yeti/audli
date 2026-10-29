"""Print review-only transactional grants; no connection or Settings import.

Usage: python -m scripts.print_runtime_grants --role reviewed_backend
This does not provision roles or grant managed Auth/Storage access.
"""
import argparse
from app.claims.privileges import runtime_grants, role_identifier


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role',required=True)
    args=parser.parse_args()
    role=role_identifier(args.role)
    statements=runtime_grants(args.role)
    print('-- REVIEW ONLY. Apply only after actual-role/schema approval and external admission stop.')
    print('BEGIN;')
    print("SET LOCAL lock_timeout='1s';")
    print("SET LOCAL statement_timeout='5s';")
    print("DO $$ BEGIN IF ARRAY(SELECT version FROM public.schema_migrations ORDER BY version) <> ARRAY[1,2,3,4,5,6,7,8] THEN RAISE EXCEPTION 'Expected complete schema008'; END IF; END $$;")
    # Reject elevated/pre-existing destructive access, rather than silently
    # claiming additive grants remove inherited privileges.
    print(f"DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='{args.role}' AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication) OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND (has_table_privilege('{args.role}',c.oid,'TRUNCATE,TRIGGER,REFERENCES,MAINTAIN') OR c.relowner=(SELECT oid FROM pg_roles WHERE rolname='{args.role}'))) OR has_schema_privilege('{args.role}','public','CREATE') THEN RAISE EXCEPTION 'Expected reviewed restricted backend role'; END IF; END $$;")
    for statement in statements:print(statement+';')
    print('COMMIT;')


if __name__=='__main__':main()
