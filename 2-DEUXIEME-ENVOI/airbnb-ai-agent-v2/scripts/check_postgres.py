"""Validate forced PostgreSQL RLS using a restricted role and synthetic rows only."""
import uuid
import psycopg
from psycopg import sql

URL = 'postgresql://alexfellous@127.0.0.1:55432/airbnb_v2_validation'

def main():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    role = sql.Identifier('validation_' + uuid.uuid4().hex)
    with psycopg.connect(URL, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE ROLE {} NOSUPERUSER NOBYPASSRLS").format(role))
        conn.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(role))
        conn.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(role))
        for org, name in [(a, 'Synthetic A'), (b, 'Synthetic B')]:
            conn.execute("INSERT INTO organizations (id,name,status,settings,agent_mode,mode_revision,created_at) VALUES (%s,%s,'active','{}','test',1,0)", (org,name))
            conn.execute("INSERT INTO properties (organization_id,id,name,address,timezone,check_in,check_out,facts,rules,notes,onboarding_step,version,created_at,is_active,status,is_billable,onboarding_data) VALUES (%s,'same-local-id',%s,'','','','','{}','{}','[]','identity',1,0,false,'onboarding',false,'{}')",(org,name))
        conn.execute(sql.SQL('SET ROLE {}').format(role))
        assert conn.execute('SELECT count(*) FROM properties').fetchone()[0] == 0
        for org, name in [(a, 'Synthetic A'), (b, 'Synthetic B')]:
            conn.execute("SELECT set_config('app.organization_id',%s,false)",(org,))
            assert conn.execute('SELECT name FROM properties').fetchall() == [(name,)]
            assert conn.execute('UPDATE properties SET version=2 RETURNING name').fetchall() == [(name,)]
        try:
            conn.execute("INSERT INTO property_states (organization_id,id,property_id,key,status,note,valid_from,updated_at) VALUES (%s,'cross-org','same-local-id','elevator','working','',0,0)",(a,))
            raise AssertionError('RLS accepted foreign organization write')
        except psycopg.errors.InsufficientPrivilege:
            pass
        conn.execute("SELECT set_config('app.organization_id',%s,false)",(a,))
        try:
            conn.execute("INSERT INTO property_states (organization_id,id,property_id,key,status,note,valid_from,updated_at) VALUES (%s,'wrong-parent','nonexistent-parent','elevator','working','',0,0)",(a,))
            raise AssertionError('Composite FK accepted unknown parent')
        except psycopg.errors.ForeignKeyViolation:
            pass
        conn.execute('RESET ROLE')
        business = conn.execute("SELECT c.relname,c.relrowsecurity,c.relforcerowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' AND EXISTS (SELECT 1 FROM pg_attribute a WHERE a.attrelid=c.oid AND a.attname='organization_id') AND c.relname NOT IN ('organization_memberships','admin_sessions')").fetchall()
        assert business and all(enabled and forced for _,enabled,forced in business), business
    print('PostgreSQL migrations and forced RLS verified: own reads/updates, foreign writes, composite references.')

if __name__ == '__main__': main()
