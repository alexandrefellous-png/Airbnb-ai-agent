import time
from sqlalchemy import select
from app.core.tenancy import manager_identity
from app.db.models import ListingLifecycle,Property

class ListingService:
    def record(self,session,prop,old,status,source):
        identity=manager_identity()
        session.add(ListingLifecycle(property_id=prop.id,canonical_listing_id=prop.guesty_listing_id,from_status=old,to_status=status,from_billable=prop.is_billable,
            to_billable=prop.is_billable,actor=identity['user_id'] if identity else 'system',source=source))
    def transition(self,session,prop,status):
        if status not in {'active','inactive','archived'}:raise ValueError('Statut invalide')
        old=prop.status
        if old==status:raise ValueError('Le logement a déjà ce statut')
        now=time.time();prop.status=status;prop.is_active=status=='active'
        if status=='active':prop.activated_at=now;prop.deactivated_at=None;prop.archived_at=None
        else:prop.deactivated_at=now;prop.archived_at=now if status=='archived' else None
        # Billing flag is independent from activation. No commercial policy or charge is inferred.
        self.record(session,prop,old,status,'manager:'+status)
    def counts(self,db,at=None):
        with db.session() as session:
            properties=session.scalars(select(Property)).all()
            if at is None:
                statuses={p.id:(p.status,p.is_billable,p.guesty_listing_id) for p in properties}
            else:
                statuses={}
                for row in session.scalars(select(ListingLifecycle).where(ListingLifecycle.occurred_at<=at).order_by(ListingLifecycle.occurred_at,ListingLifecycle.id)):
                    statuses[row.property_id]=(row.to_status,row.to_billable,row.canonical_listing_id)
            active_ids={lid for status,billable,lid in statuses.values() if lid and status=='active'}
            return {'total':len(statuses),'active_listings':len(active_ids),'configured_listings':len({lid for status,billable,lid in statuses.values() if lid}),
                'onboarding':sum(s=='onboarding' for s,_,_ in statuses.values()),'inactive':sum(s=='inactive' for s,_,_ in statuses.values()),
                'archived':sum(s=='archived' for s,_,_ in statuses.values()),'billable_listings':len({lid for status,billable,lid in statuses.values() if billable and lid}),
                'billing_policy':'not_configured','as_of':at}
