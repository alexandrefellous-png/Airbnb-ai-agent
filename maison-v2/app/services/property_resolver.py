from sqlalchemy import select
from app.db.models import GuestyMapping, Property,PropertySync

class ResolutionError(RuntimeError):
    pass

class PropertyResolver:
    def resolve(self, session, reservation, conversation, reservation_id,verified_listing_id=None):
        # Explicit paths from the Guesty schemas. Neither addresses nor names are identifiers.
        references = []
        stays = reservation.get("stay", []) if reservation else []
        if reservation and (not isinstance(stays, list) or len(stays) != 1):
            raise ResolutionError("multi_stay_requires_manager_mapping")
        for stay in stays:
            for field, kind in (("unitId", "unit"), ("unitTypeId", "unit_type")):
                value = stay.get(field)
                if value is not None:
                    if not isinstance(value, str):
                        raise ResolutionError("unsupported_guesty_id_representation")
                    references.append((kind, value))
        metas = conversation.get("meta", {}).get("reservations", [])
        selected = [m for m in metas if m.get("_id") == reservation_id] if reservation_id else metas
        if not reservation_id and len(selected) != 1:
            raise ResolutionError("ambiguous_inquiry_property")
        for meta in selected:
            listing = meta.get("listing", {}).get("_id")
            if listing:
                references.append(("listing", listing))
        if not references:
            raise ResolutionError("property_identifiers_missing")
        found = set()
        for kind, external_id in references:
            row = session.scalar(select(GuestyMapping).where(GuestyMapping.kind == kind,
                GuestyMapping.external_id == external_id))
            if row:
                found.add(row.property_id)
            elif kind == "listing":
                prop = session.scalar(select(Property).where(Property.guesty_listing_id == external_id))
                if prop:
                    found.add(prop.id)
                else:
                    raise ResolutionError("unknown_listing")
            else:
                if verified_listing_id and len(selected)==1 and selected[0].get('listing',{}).get('_id')==verified_listing_id:
                    # Relationship comes from the fresh reservation/conversation pair,
                    # not from assuming that different ID namespaces are equal.
                    continue
                # Never assume unitTypeId == listingId. Explicit mapping must be configured once.
                raise ResolutionError("unmapped_" + kind)
        if len(found) != 1:
            raise ResolutionError("conflicting_property_ids")
        prop = session.get(Property, found.pop())
        sync=session.get(PropertySync,prop.id) if prop else None
        if sync and sync.status!='synced':raise ResolutionError('guesty_property_'+sync.status)
        if not prop or not prop.is_active or prop.archived_at or not prop.guesty_listing_id or not prop.timezone or not prop.check_in or not prop.check_out:
            raise ResolutionError("property_not_configured")
        return prop
