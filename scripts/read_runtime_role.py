"""Read-only Render Shell snapshot. Prints no URL, credentials or learner data.

Opens one short database connection; does not import the app or Settings.
"""
import json
import os
import psycopg


def main():
    url=os.environ['AUDLI_DATABASE_URL'].replace('postgresql+psycopg://','postgresql://',1)
    with psycopg.connect(url,connect_timeout=5,
        options='-c default_transaction_read_only=on -c statement_timeout=3000 -c lock_timeout=1000') as db:
        with db.cursor() as cursor:
            cursor.execute("SELECT current_user,session_user,rolsuper,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=current_user")
            row=cursor.fetchone()
            result=dict(zip(('effective_role','session_role','superuser','bypass_rls','inherits'),row))
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend'")
            result['postgres_client_sessions']=cursor.fetchone()[0]
            cursor.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND usename=current_user AND application_name='Supavisor'")
            result['same_role_supavisor_backend_sessions']=cursor.fetchone()[0]
            result['note']='Backend counts do not establish session-pooler client occupancy or reserved capacity.'
        db.rollback()
    print(json.dumps(result))


if __name__=='__main__':
    try:main()
    except Exception as error:
        raise SystemExit('Read-only snapshot failed: '+type(error).__name__+'. No credentials were printed.') from None
