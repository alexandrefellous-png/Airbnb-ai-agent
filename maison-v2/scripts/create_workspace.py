"""Provision a workspace and its owner. Password is read interactively, never from command arguments."""
import argparse
import getpass
from sqlalchemy import select
from app.core.config import Settings
from app.core.security import Vault
from app.core.passwords import hash_password
from app.db.models import Organization, User, Membership, GuestyConnection
from app.db.session import Database
from app.core.tenancy import organization_scope

def provision(db, vault, name, email, password, organization_id=None, credentials=None):
    email = email.strip().casefold()
    with db.system_session() as session:
        org = session.get(Organization,organization_id) if organization_id else None
        if organization_id and not org: raise ValueError("Workspace introuvable")
        if not org:
            org=Organization(name=name); session.add(org);session.flush()
        user = session.scalar(select(User).where(User.email == email))
        if user:
            raise ValueError("Compte existant : utilisez un flux d'invitation pour ajouter un collaborateur")
        user=User(email=email,password_hash=hash_password(password));session.add(user);session.flush()
        session.add(Membership(organization_id=org.id,user_id=user.id,role="owner"));session.commit()
        oid=org.id
    if credentials:
        with organization_scope(oid), db.session() as session:
            session.add(GuestyConnection(encrypted_credentials=vault.encrypt(credentials)));session.commit()
    return oid

if __name__ == "__main__":
    parser=argparse.ArgumentParser();parser.add_argument("--name",required=True);parser.add_argument("--email",required=True);parser.add_argument("--organization-id")
    args=parser.parse_args(); settings=Settings(); db=Database(settings.database_url)
    password=getpass.getpass("Mot de passe propriétaire (12 caractères minimum) : ")
    print(provision(db,Vault(settings.token_encryption_key),args.name,args.email,password,args.organization_id))
