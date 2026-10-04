import os
import subprocess
import time
from pathlib import Path
from sqlalchemy import create_engine,text,inspect

def migrate(url,target='head'):
    result=subprocess.run([str(Path('.venv/bin/alembic').resolve()),'upgrade',target],env={**os.environ,'DATABASE_URL':url},capture_output=True,text=True)
    assert result.returncode==0,result.stderr

def test_data_preserving_migrations_from_original_schema(tmp_path):
    url='sqlite:///'+str(tmp_path/'migration.db');migrate(url,'a703e913333a')
    engine=create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO properties (id,name,guesty_listing_id,address,timezone,check_in,check_out,facts,rules,notes,onboarding_step,version,created_at) VALUES ('synthetic-id','Synthetic','synthetic-guesty','Synthetic address','Europe/Paris','16:00','10:00','{}','{}','[]','ready',1,:now)"),{'now':time.time()})
    migrate(url)
    with engine.connect() as conn:
        row=conn.execute(text('SELECT properties.name,properties.organization_id,properties.status,organizations.agent_mode FROM properties JOIN organizations ON properties.organization_id=organizations.id')).one()
        assert row.name=='Synthetic' and row.organization_id and row.status=='active' and row.agent_mode=='test'
        assert conn.execute(text('SELECT count(*) FROM listing_lifecycle')).scalar()==1
        assert conn.exec_driver_sql('PRAGMA foreign_key_check').fetchall()==[]
    from app.db.models import Base
    assert set(inspect(engine).get_table_names())==set(Base.metadata.tables)|{'alembic_version'}
    engine.dispose()
