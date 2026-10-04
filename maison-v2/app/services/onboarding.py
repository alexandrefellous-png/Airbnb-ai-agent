"""Progressive operational questionnaire; empty and deferred fields are never invented."""
from app.db.models import PropertyAccess,PropertyWifi,MediaAsset
from sqlalchemy import select

GROUPS={
 'Identité':['guesty_listing_id','address','timezone'],
 'Séjour':['check_in','check_out','capacity','bedrooms','beds','bathrooms','wc','sofa_bed'],
 'Accès':['floor','elevator','stairs','street_instructions','door','visual_cues','building_code','lockbox_location','lockbox_type','lockbox_code','access_video','access_photos','arrival_guide'],
 'Wi-Fi':['wifi_network','wifi_password','wifi_router_location'],
 'Équipements':['air_conditioning','heating','washing_machine','dryer','dishwasher','tv','kitchen','oven','microwave','coffee_machine','equipment'],
 'Technique':['electrical_panel_location','water_heater_location','hot_water_type','controls_location','troubleshooting','property_specifics'],
 'Vie sur place':['trash_location','heating_instructions','ac_instructions'],
 'Règles':['house_rules','luggage_policy','early_checkin_policy','late_checkout_policy','important_information','limitations','special_instructions']}
CRITICAL={'guesty_listing_id','address','timezone','check_in','check_out'}
QUESTIONS={
 'trash_location':'Où les voyageurs doivent-ils déposer les poubelles ?', 'heating_instructions':'Comment utiliser le chauffage ?', 'ac_instructions':'Comment utiliser la climatisation ?',
 'luggage_policy':'Quelle est la consigne pour les bagages ?', 'early_checkin_policy':'Quelle règle appliquer aux arrivées anticipées ?', 'late_checkout_policy':'Quelle règle appliquer aux départs tardifs ?',
 'guesty_listing_id':'Quel est le Listing ID Guesty vérifié ?', 'address':'Quelle est l’adresse complète ?', 'timezone':'Quel est le fuseau horaire ?',
 'check_in':'À partir de quelle heure les voyageurs peuvent-ils arriver ?', 'check_out':'À quelle heure doivent-ils partir ?',
 'capacity':'Quelle est la capacité maximale ?', 'bedrooms':'Combien de chambres ?', 'beds':'Combien de lits ?', 'bathrooms':'Combien de salles de bain ?', 'wc':'Combien de WC ?', 'sofa_bed':'Y a-t-il un canapé-lit ?',
 'floor':'À quel étage se trouve l’appartement ?', 'elevator':'Y a-t-il un ascenseur ?', 'stairs':'Quel escalier faut-il prendre ?', 'street_instructions':'Quel chemin précis suivre depuis l’entrée de l’immeuble ?', 'door':'Où se trouve la porte ?', 'visual_cues':'Comment reconnaître la porte ?', 'building_code':'Quel est le code immeuble, s’il y en a un ?', 'lockbox_location':'Où se trouve la boîte à clés ?', 'lockbox_type':'Quel est le type de boîte à clés ?', 'lockbox_code':'Quel est son code ?', 'access_video':'Avez-vous une vidéo d’accès ?', 'access_photos':'Avez-vous une photo d’accès ?', 'arrival_guide':'Avez-vous un guide d’arrivée ?',
 'wifi_network':'Quel est le nom du réseau Wi-Fi ?', 'wifi_password':'Quel est le mot de passe Wi-Fi ?', 'wifi_router_location':'Où se trouve la box ?',
 'air_conditioning':'Y a-t-il une climatisation ?', 'heating':'Quel chauffage est disponible ?', 'washing_machine':'Y a-t-il un lave-linge ?', 'dryer':'Un sèche-linge ?', 'dishwasher':'Un lave-vaisselle ?', 'tv':'Une télévision ?', 'kitchen':'Comment est équipée la cuisine ?', 'oven':'Un four ?', 'microwave':'Un micro-ondes ?', 'coffee_machine':'Quelle machine à café ?', 'equipment':'Quels autres équipements sont utiles ?',
 'electrical_panel_location':'Où est le tableau électrique ?', 'water_heater_location':'Où est le ballon d’eau chaude ?', 'hot_water_type':'Quel est le système de production d’eau chaude ?', 'controls_location':'Où se trouvent les commandes importantes ?', 'troubleshooting':'Quelles vérifications simples et sûres recommandez-vous ?', 'property_specifics':'Quelles particularités du logement faut-il connaître ?',
 'house_rules':'Quelles sont les règles du logement ?', 'important_information':'Quelles informations importantes communiquer ?', 'limitations':'Quelles limitations faut-il préciser ?', 'special_instructions':'Y a-t-il des consignes particulières ?'}

def question_type(field):
    if field in {"access_video","access_photos","arrival_guide"}:return "media"
    if field in {"elevator","sofa_bed","air_conditioning","washing_machine","dryer","dishwasher","tv","oven","microwave"}:return "boolean"
    if field in {"capacity","bedrooms","beds","wc"}:return "integer"
    if field=="bathrooms":return "decimal"
    if field in {"check_in","check_out"}:return "time"
    if field in {"building_code","lockbox_code","wifi_password"}:return "password"
    return "text"

class OnboardingService:
    def __init__(self,vault):self.vault=vault
    def progress(self,session,prop,related=None):
        a=related['access'] if related is not None else session.get(PropertyAccess,prop.id)
        w=related['wifi'] if related is not None else session.get(PropertyWifi,prop.id)
        values={**prop.facts,**(self.vault.decrypt(a.encrypted_data) if a else {}),**(self.vault.decrypt(w.encrypted_data) if w else {}),
            **{k:getattr(prop,k) for k in CRITICAL}}
        from app.services.knowledge import KnowledgeService
        memory=KnowledgeService(self.vault)
        rows=related['knowledge'] if related is not None else memory.rows(session,prop.id)
        known=memory.effective(rows)
        values.update({key:item['value'] for key,item in known.items()})
        if values.get('late_checkout_until'):values['late_checkout_policy']=values['late_checkout_until']
        if values.get('early_checkin_from'):values['early_checkin_policy']=values['early_checkin_from']
        # A legacy free-text "yes" is not a configured media link.
        for field in {"access_video","access_photos","arrival_guide"}:
            value=values.get(field)
            if value is not False and not (isinstance(value,str) and value.startswith("https://")) and not (isinstance(value,list) and value and all(isinstance(v,str) and v.startswith("https://") for v in value)):
                values.pop(field,None)
        media=related['media'] if related is not None else session.scalars(select(MediaAsset).where(MediaAsset.property_id==prop.id,MediaAsset.status=='assigned')).all()
        for item in media:
            field={'access_video':'access_video','access_photo':'access_photos','arrival_guide':'arrival_guide'}.get(item.kind)
            if field:values[field]='configured'
        fields=[field for group in GROUPS.values() for field in group]
        # False/zero are explicit facts; absent/blank values remain unknown.
        missing=[field for field in fields if field not in values or values[field] is None or values[field]=='']
        data=prop.onboarding_data or {};deferred=data.get('deferred',[])
        next_group=None;next_fields=[]
        for group in ['Identité','Accès','Wi-Fi','Vie sur place','Règles','Technique','Séjour','Équipements']:
            keys=GROUPS[group]
            candidates=[key for key in keys if key in missing and key not in deferred]
            if candidates:next_group,next_fields=group,candidates[:3];break
        critical=[key for key in missing if key in CRITICAL]
        configuration='active' if prop.is_active else 'incomplete' if critical else 'ready'
        categories=[{'name':group,'known':sum(key not in missing for key in keys),'total':len(keys)} for group,keys in GROUPS.items()]
        return {'configuration_status':configuration,'percent':round(100*sum(c['known']/c['total'] for c in categories)/len(categories)), 'categories':categories,
            'missing':missing,'critical_missing':critical,'deferred':deferred,'next_group':next_group,'next_fields':next_fields,
            'questions':[{'field':key,'question':QUESTIONS[key],'type':question_type(key),'sensitive':key in {'building_code','lockbox_code','wifi_password','address','guesty_listing_id'}} for key in next_fields],
            'media_requested':data.get('media_requested',{}),
            'field_sources':data.get('field_sources',{})}
    def save_sources(self,prop,updates,sources=None):
        data=dict(prop.onboarding_data or {});origins=dict(data.get('field_sources',{}));deferred=list(data.get('deferred',[]))
        for update in updates:
            origins[update.field]=(sources or {}).get(update.field,'manual')
            if update.field in deferred:deferred.remove(update.field)
        data.update(field_sources=origins,deferred=deferred);prop.onboarding_data=data
    def defer(self,session,prop):
        progress=self.progress(session,prop);data=dict(prop.onboarding_data or {})
        data['deferred']=list(dict.fromkeys([*data.get('deferred',[]),*progress['next_fields']]))
        prop.onboarding_data=data
    def resume(self,prop):
        data=dict(prop.onboarding_data or {});data['deferred']=[];prop.onboarding_data=data
