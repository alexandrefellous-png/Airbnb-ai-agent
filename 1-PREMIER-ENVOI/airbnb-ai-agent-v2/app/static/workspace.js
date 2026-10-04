'use strict';

async function loadHome(){
  $('#home-content').innerHTML='<p class="fine">Maison fait le point…</p>';
  const data=await api('/overview');state.properties=data.properties;
  $('#home-content').innerHTML=`<section class="maison-intro"><div class="agent-presence"><span></span> MAISON · VOTRE EMPLOYÉ IA</div><h2>Vos voyageurs.<br><em>L’esprit libre.</em></h2><p>Je m’occupe des demandes du quotidien. Je reviens vers vous quand j’ai besoin d’une information ou de votre décision.</p><div class="agent-activity">${data.agent_mode==='test'?`<span class="presence-dot"></span> En observation · ${data.handled.simulated} réponses préparées, aucun envoi`:`<span class="presence-dot"></span> ${data.handled.sent} réponses envoyées${data.live_allowed?'':' · envois actuellement bloqués'}`}</div></section>
  ${!data.guesty_connected||!data.openai_connected?`<section class="agent-connect"><div><strong>Faisons connaissance.</strong><p>Connectez ${!data.guesty_connected?'Guesty':''}${!data.guesty_connected&&!data.openai_connected?' et ':''}${!data.openai_connected?'OpenAI':''}. Je retrouverai vos logements et vous demanderai seulement ce qui me manque.</p></div><button class="primary" data-open-page="connections">Connecter mes outils →</button></section>`:''}
  <form id="home-agent-form" class="agent-brief"><label for="home-agent-input">Une information à me transmettre ?</label><textarea id="home-agent-input" placeholder="Un accès a changé, un équipement est réparé… Dites-le-moi simplement." rows="2" required maxlength="20000"></textarea><div><span>Je retiens vos consignes, logement par logement.</span><button class="primary">Parler à Maison ↗</button></div></form>
  <section class="agent-attention"><div class="section-heading"><div><p class="eyebrow">SEULEMENT CE QUI A BESOIN DE VOUS</p><h3>${data.attention.length?'Un coup de main, et je prends la suite.':'Vous pouvez souffler.'}</h3></div>${data.attention.length?`<span class="attention-count">${data.attention.length}</span>`:''}</div>${data.attention.length?data.attention.slice(0,3).map(a=>`<article class="attention-item"><div><small>${esc(a.property_name)}</small><p>${esc(a.summary)}</p></div><button class="secondary" ${a.learning&&a.property_id?`data-teach-property="${esc(a.property_id)}"`:'data-open-page="alerts"'}>${a.learning?'Lui apprendre':'Voir la demande'} →</button></article>`).join(''):'<p class="muted">Aucune décision en attente. Les échanges voyageurs restent dans Guesty.</p>'}${data.attention.length>3?'<button class="text-button" data-open-page="alerts">Voir les autres demandes →</button>':''}</section>

  <footer class="agent-footer"><button class="text-button" data-open-page="properties">Mes biens · ${data.properties.length} logements</button><button class="text-button" data-open-page="feed">Améliorer Maison ${data.agent_mode==='test'?'· TEST':''} →</button><span>${data.sync.state==='ready'?'Logements synchronisés automatiquement':data.sync.state==='error'?esc(data.sync.error):'Découverte des logements après connexion'}</span></footer>`;
}

function connectionStatus(connected){return `<span class="connection-status ${connected?'connected':''}">${connected?'✓ Connecté':'À connecter'}</span>`;}
async function loadConnections(){
  const [s,drive]=await Promise.all([api('/settings'),api('/settings/drive')]);
  const admin=['owner','admin'].includes(s.role);
  $('#connections-content').innerHTML=`<div class="connection-grid">
    <article class="connection-card"><div class="connection-top"><span class="connection-symbol">G</span>${connectionStatus(s.guesty_connected)}</div><h3>Guesty</h3><p>Vos logements, vos réservations et les conversations voyageurs.</p>${admin?`<details ${s.guesty_connected?'':'open'}><summary>${s.guesty_connected?'Modifier la connexion':'Connecter mon compte Guesty'}</summary><ol class="connection-guide"><li>Dans Guesty, ouvrez <strong>Outils de développement → Applications OAuth</strong>.</li><li>Créez une application, puis copiez ses deux identifiants.</li><li>Collez-les ici. Nous vérifions la connexion pour vous.</li></ol><form id="guesty-form"><label>Client ID Guesty<input name="client_id" required autocomplete="off" placeholder="Copiez le Client ID"></label><label>Client Secret Guesty<input name="client_secret" type="password" required autocomplete="off" placeholder="Copiez le Client Secret"></label><input name="webhook_secret" type="hidden" value=""><button class="primary">Connecter Guesty</button></form><a class="text-link" href="https://open-api-docs.guesty.com/docs/quick-start-guide" target="_blank" rel="noopener">Ouvrir le guide Guesty ↗</a></details>`:'<p class="fine">Votre administrateur peut gérer cette connexion.</p>'}<details><summary>Recevoir les nouveaux messages</summary><p class="fine">Après publication du site en HTTPS, ajoutez cette URL dans Guesty → Webhooks, avec l’événement reservation.messageReceived.</p><code>${esc(location.origin+s.webhook_path)}</code><p class="fine">Sur localhost, Guesty ne peut pas joindre le site. L’import des logements fonctionne déjà.</p>${admin?'<form id="webhook-secret-form"><label>Secret de signature fourni par Guesty<input name="secret" type="password" required autocomplete="off"></label><button class="secondary">Enregistrer le secret</button></form>':''}</details></article>
    <article class="connection-card"><div class="connection-top"><span class="connection-symbol">✦</span>${connectionStatus(s.openai_connected)}</div><h3>OpenAI</h3><p>Votre agent comprend vos consignes et prépare les réponses voyageurs.</p>${admin?`<details ${s.openai_connected?'':'open'}><summary>${s.openai_connected?'Modifier la clé':'Activer mon agent'}</summary><p class="fine">Créez une clé API OpenAI puis collez-la ici. L’API utilise sa propre facturation.</p><a class="text-link" href="https://platform.openai.com/api-keys" target="_blank" rel="noopener">Créer ma clé API ↗</a><form id="openai-form"><label>Clé API<input name="api_key" type="password" required minlength="20" autocomplete="off" placeholder="Votre clé privée"></label><button class="primary">Connecter OpenAI</button></form></details>`:'<p class="fine">Votre administrateur peut gérer cette connexion.</p>'}</article>
    <article class="connection-card"><div class="connection-top"><span class="connection-symbol">△</span>${connectionStatus(drive.connected)}</div><h3>Google Drive</h3><p>Un dossier par logement pour vos photos, vidéos et guides. Un lien est créé après chaque upload.</p>${admin?drive.connected?`<p class="connection-success">Vos fichiers peuvent être stockés automatiquement dans votre Drive.</p><button class="secondary" id="drive-disconnect">Déconnecter Drive</button>`:drive.configured?'<button class="primary" id="drive-connect">Connecter Google Drive →</button><p class="fine">Vous choisissez le compte Google et autorisez les fichiers de cette application.</p>':`<div class="connection-pending"><strong>Préparation Google nécessaire</strong><p>Une application Google OAuth doit être configurée une seule fois. Ensuite, un bouton suffit pour choisir votre compte Drive.</p></div><details><summary>Configurer l’application Google</summary><ol class="connection-guide"><li>Créez un projet Google Cloud et activez <strong>Google Drive API</strong>.</li><li>Créez un client OAuth de type <strong>Application Web</strong>.</li><li>Ajoutez cette URI de redirection exacte :</li></ol><code>${esc(drive.redirect_uri)}</code><p class="fine">Si l’application Google est en test, ajoutez votre email comme utilisateur de test.</p><a class="text-link" href="https://console.cloud.google.com/apis/credentials" target="_blank" rel="noopener">Ouvrir Google Cloud ↗</a><form id="drive-config-form"><label>Client ID Google<input name="client_id" required autocomplete="off"></label><label>Client Secret Google<input name="client_secret" type="password" required autocomplete="off"></label><button class="primary">Enregistrer puis connecter</button></form></details>`:'<p class="fine">Votre administrateur peut gérer cette connexion.</p>'}<p class="fine">Les fichiers restent privés. Vous choisissez ceux qui sont accessibles aux voyageurs disposant du lien.</p></article>
  </div><div class="privacy-note">✓ Les clés sont chiffrées et propres à votre conciergerie. Elles ne sont jamais affichées après enregistrement.</div>`;
}

async function beginListing(){
  openDialog('Logement hors intégration',`<p class="muted">Les logements Guesty sont découverts automatiquement. Ce formulaire est réservé à un logement absent de vos outils connectés.</p><form id="manual-property-form"><label>Nom du logement<input name="name" required maxlength="120"></label><button class="primary">Créer dans la mémoire Maison</button></form>`);
}

function mediaList(p){
  return p.media.length?`<div class="media-list">${p.media.map(m=>`<article class="media-item"><div><a href="${esc(m.url)}" target="_blank" rel="noopener">${esc(m.name)}</a><p class="fine">${esc(m.kind)} · ${Math.ceil(m.size/1024)} Ko</p>${m.drive?.url?`<a class="text-link" href="${esc(m.drive.url)}" target="_blank" rel="noopener">Ouvrir dans Drive ↗</a><p class="fine">${m.drive.shared?'Lien accessible aux voyageurs':'Fichier privé'}</p>`:`<button class="text-button" data-publish-media="${esc(m.id)}" data-id="${esc(p.id)}">Créer le lien Drive</button>`}</div></article>`).join('')}</div>`:'<p class="fine">Aucun fichier enregistré pour ce logement.</p>';
}

async function showPropertyOverview(id){
  const p=await api('/properties/'+id);
  openDialog(p.name,`<p class="muted">${esc(p.address||'Adresse à compléter')}</p><div class="property-progress"><div><strong>La mémoire de Maison</strong><span>Expliquez-lui ce qui est utile pour gérer les voyageurs.</span></div><button class="primary" data-teach-property="${esc(p.id)}">Former Maison →</button></div><div class="quick-facts">${[['Arrivée',p.check_in||'À compléter'],['Départ',p.check_out||'À compléter'],['Wi-Fi',p.wifi_configured?'Renseigné':'À compléter'],['Statut',p.is_active?'Actif':'À préparer']].map(([k,v])=>`<div><span>${k}</span><strong>${esc(v)}</strong></div>`).join('')}</div><section class="detail-section"><h3>Photos, vidéos et guide d’arrivée</h3>${mediaList(p)}<div class="result-actions"><button class="secondary" data-media-upload="access_video" data-id="${esc(p.id)}">＋ Vidéo</button><button class="secondary" data-media-upload="access_photos" data-id="${esc(p.id)}">＋ Photo</button><button class="secondary" data-media-upload="arrival_guide" data-id="${esc(p.id)}">＋ Guide</button></div></section><section class="detail-section"><h3>État de l’ascenseur</h3><p class="fine">${esc(text(stateStatus(p,'elevator')))}</p><div class="result-actions"><button class="secondary" data-state="working" data-property="${esc(p.name)}">Ascenseur réparé</button><button class="danger" data-state="out_of_order" data-property="${esc(p.name)}">Ascenseur en panne</button></div></section><details class="memory-details"><summary>Ce que Maison sait et d’où cela vient</summary>${Object.entries(p.knowledge||{}).map(([key,item])=>`<div class="detail-row"><span>${esc(labels[key]||key)}<small>${esc(item.source==='guesty'?'Guesty':item.source==='manager'?'Vous':'Conversation validée')}${item.valid_until?' · jusqu’au '+esc(new Date(item.valid_until*1000).toLocaleString('fr-FR')):''}</small></span><strong>${esc(typeof item.value==='object'?JSON.stringify(item.value):String(item.value))}</strong></div>`).join('')}</details><button class="text-button" data-wizard="${esc(p.id)}">Compléter avec les formulaires</button><button class="text-button" data-full-property="${esc(p.id)}">Voir toutes les informations et les réglages du logement →</button>`);
}

function questionHtml(q){
  if(q.type==='media')return `<label>${esc(q.question)}<select name="media:${esc(q.field)}" data-media-question="${esc(q.field)}"><option value="">Choisir une réponse</option><option value="true">Oui, je vais ajouter le fichier</option><option value="false">Non, je n’en ai pas</option></select><small class="media-help" hidden>Le fichier vous sera demandé à l’étape suivante. Dire oui ne remplace pas l’ajout du fichier.</small></label>`;
  if(q.type==='boolean')return `<label>${esc(q.question)}<select name="${esc(q.field)}"><option value="">Choisir une réponse</option><option value="true">Oui</option><option value="false">Non</option></select></label>`;
  const type={integer:'number',decimal:'number',time:'time',password:'password',text:'text'}[q.type]||'text';
  const attrs=q.type==='integer'?'min="0" max="100" step="1"':q.type==='decimal'?'min="0" max="100" step="0.5"':'';
  const hints={timezone:'Ex. Europe/Paris',floor:'Ex. 3e étage, porte de gauche',building_code:'Indiquez Aucun s’il n’y a pas de code',wifi_network:'Le nom affiché sur le réseau Wi-Fi'};
  return `<label>${esc(q.question)}<input name="${esc(q.field)}" type="${type}" ${attrs} autocomplete="off" placeholder="${esc(hints[q.field]||'Votre réponse')}"></label>`;
}

function onboardingHtml(p){
  const c=p.configuration;if(!c)return '';
  const pending=Object.keys(c.media_requested||{}).filter(k=>c.media_requested[k]);
  return `<section class="wizard-progress"><div><strong>Informations du logement</strong><span>${esc(c.next_group||'Récapitulatif')}</span></div></section>
    ${pending.length?`<section class="upload-invitation"><h3>Ajoutez les fichiers que vous avez indiqués</h3><p>Ils seront automatiquement rattachés à ${esc(p.name)}.</p><div class="result-actions">${pending.map(field=>`<button class="primary" data-media-upload="${esc(field)}" data-id="${esc(p.id)}">Ajouter ${esc(labels[field].toLowerCase())}</button>`).join('')}</div></section>`:''}
    ${c.questions.length?`<section class="wizard-questions"><p class="eyebrow">${esc(c.next_group)}</p><h3>Quelques informations pour bien accueillir vos voyageurs.</h3><form id="onboard-form" data-id="${esc(p.id)}" data-name="${esc(p.name)}">${c.questions.map(questionHtml).join('')}<button class="primary">Enregistrer et continuer →</button></form><button class="text-button" data-wizard-defer="${esc(p.id)}" data-name="${esc(p.name)}">Je compléterai ces informations plus tard</button></section>`:'<section class="wizard-questions"><h3>Vous avez parcouru toutes les questions.</h3><p>Consultez les informations reportées ou vérifiez votre fiche avant activation.</p></section>'}
    <div class="wizard-footer"><span>Maison demandera les informations utiles au fil des besoins.</span>${c.deferred.length?`<button class="text-button" data-wizard-resume="${esc(p.id)}" data-name="${esc(p.name)}">Reprendre les questions reportées</button>`:''}<button class="text-button" data-property-overview="${esc(p.id)}">Voir le récapitulatif →</button></div>`;
}
async function showPropertyWizard(id){
  const p=await api('/properties/'+id);
  openDialog('Préparer '+p.name,`<p class="muted">Vos réponses sont enregistrées au fur et à mesure. Vous pouvez reprendre cette fiche à tout moment.</p>${onboardingHtml(p)}${!p.configuration.critical_missing.length&&!p.is_active?`<div class="activation-note"><strong>Les informations essentielles sont renseignées.</strong><p>Vérifiez le récapitulatif avant de confirmer l’activation.</p><button class="secondary" data-full-property="${esc(p.id)}">Vérifier et activer le logement</button></div>`:''}`);
}

async function uploadForQuestion(propertyId, field){
  const p=await api('/properties/'+propertyId), drive=await api('/settings/drive');
  const kind={access_video:'access_video',access_photos:'access_photo',arrival_guide:'arrival_guide'}[field];
  state.uploadProperty=propertyId;
  const accepted=kind==='access_video'?'video/mp4,video/webm':kind==='access_photo'?'image/jpeg,image/png,image/webp':'application/pdf,image/jpeg,image/png,image/webp';
  $('#file-picker').accept=accepted;
  openDialog('Ajouter '+(kind==='access_video'?'une vidéo':kind==='access_photo'?'une photo':'un guide'),`<div class="upload-destination"><span>⌂</span><div><small>LOGEMENT CHOISI</small><strong>${esc(p.name)}</strong></div></div><input id="upload-kind" type="hidden" value="${kind}"><p class="muted">${drive.connected?'Le fichier sera enregistré dans votre espace et dans le dossier Drive de ce logement. Son lien sera ajouté à la fiche.':'Le fichier sera conservé dans votre espace. Connectez Drive pour créer ensuite son lien automatiquement.'}</p><label class="check-row"><input id="confirm-upload" type="checkbox" required><span>Je confirme que ce fichier concerne <strong>${esc(p.name)}</strong>.</span></label>${drive.connected?'<label class="check-row"><input id="share-upload" type="checkbox"><span>Autoriser les voyageurs disposant du lien à consulter ce fichier. L’agent utilisera ce lien uniquement dans le contexte d’accès autorisé.</span></label>':''}<p class="fine">${kind==='access_video'?'MP4 ou WebM':kind==='access_photo'?'JPEG, PNG ou WebP':'PDF ou image'} · 25 Mo maximum.</p><button id="choose-file" class="primary">Choisir le fichier et enregistrer</button><button class="text-button" data-wizard="${esc(p.id)}">Revenir aux questions</button>`);
}

document.addEventListener('change',event=>{
  const select=event.target.closest('[data-media-question]');
  if(select)select.parentElement.querySelector('.media-help').hidden=select.value!=='true';
});
document.addEventListener('click',async event=>{
  const el=event.target.closest('button');if(!el)return;
  try{
    if(el.id==='home-add')await beginListing();
    if(el.dataset.selectListing){
      el.disabled=true;
      const result=await api('/onboarding/select',{method:'POST',body:{listing_id:el.dataset.selectListing}});
      state.wizardProperty=result.property_id;
      await showChange(result.change_id);
    }
    if(el.dataset.wizard)await showPropertyWizard(el.dataset.wizard);
    if(el.dataset.propertyOverview)await showPropertyOverview(el.dataset.propertyOverview);
    if(el.dataset.fullProperty)await showProperty(el.dataset.fullProperty);
    if(el.dataset.mediaUpload)await uploadForQuestion(el.dataset.id,el.dataset.mediaUpload);
    if(el.dataset.wizardDefer||el.dataset.wizardResume){
      const id=el.dataset.wizardDefer||el.dataset.wizardResume;
      await perform(action(el.dataset.wizardDefer?'defer_onboarding':'resume_onboarding',{property:el.dataset.name}));
      await showPropertyWizard(id);
    }
    if(el.id==='choose-file'&&state.uploadProperty&&!$('#confirm-upload').checked){
      event.stopImmediatePropagation();toast('Confirmez le logement avant de choisir le fichier.');
    }
    if(el.id==='drive-connect'){
      el.disabled=true;const result=await api('/settings/drive/connect',{method:'POST'});location.assign(result.url);
    }
    if(el.id==='drive-disconnect'){
      await api('/settings/drive/disconnect',{method:'POST'});await loadConnections();
    }
    if(el.dataset.publishMedia){
      const connected=await api('/settings/drive');
      if(!connected.connected){$('#sheet').close();await navigate('connections');toast('Connectez Google Drive puis revenez au fichier.');return;}
      openDialog('Créer le lien Drive',`<p>Le fichier sera ajouté au Drive connecté à votre conciergerie.</p><label class="check-row"><input id="publish-share" type="checkbox"><span>Autoriser les voyageurs disposant du lien à le consulter.</span></label><button class="primary" data-publish-confirm="${esc(el.dataset.publishMedia)}" data-id="${esc(el.dataset.id)}">Créer le lien</button>`);
    }
    if(el.dataset.publishConfirm){
      el.disabled=true;
      const result=await api('/media/'+el.dataset.publishConfirm+'/drive',{method:'POST',body:{share_guest:$('#publish-share').checked}});
      toast(result.message);await showPropertyOverview(el.dataset.id);
    }
  }catch(exc){toast(exc.message);el.disabled=false;}
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(!['drive-config-form','manual-property-form','webhook-secret-form'].includes(form.id))return;
  event.preventDefault();const button=form.querySelector('button');button.disabled=true;
  try{
    const values=Object.fromEntries(new FormData(form));
    if(form.id==='drive-config-form'){
      await api('/settings/drive/configuration',{method:'POST',body:values});form.reset();await loadConnections();
      const result=await api('/settings/drive/connect',{method:'POST'});location.assign(result.url);
    }
    if(form.id==='manual-property-form'){
      const result=await perform(action('create_property',{property:values.name}));
      await loadProperties();await teachProperty(result.property_id);
    }
    if(form.id==='webhook-secret-form'){
      const result=await api('/settings/guesty/webhook',{method:'POST',body:values});form.reset();toast(result.message);
    }
  }catch(exc){toast(exc.message);}finally{button.disabled=false;}
});

let receivedThreads=[];
async function loadInbox(){
  const [status,threads]=await Promise.all([api('/observation'),api('/inbox')]);
  receivedThreads=threads;
  const admin=['owner','admin'].includes(state.identity.role);
  const when=status.last_sync_at?new Date(status.last_sync_at*1000).toLocaleTimeString('fr-FR',{hour:'2-digit',minute:'2-digit'}):'Pas encore synchronisé';
  $('#observation-controls').innerHTML=`<section class="observation-bar"><div><strong>${status.enabled?'Observation automatique activée':'Vos messages Guesty en lecture seule'}</strong><p>${esc(status.error||`Dernière lecture : ${when}. Les réponses restent des propositions, aucun envoi.`)}</p>${!status.openai_connected?'<p>Connectez OpenAI pour préparer les réponses IA.</p>':''}</div><div class="result-actions"><button class="primary" id="sync-inbox">Lire mes messages Guesty</button>${admin?`<button class="secondary" data-observe-enabled="${!status.enabled}">${status.enabled?'Mettre en pause':'Activer le suivi automatique'}</button>`:''}</div></section>`;
  const previews=new Set(feedItems.map(item=>item.conversation_id));
  $('#inbox-list').innerHTML=threads.length?`<section class="received-section"><div class="section-heading"><div><h3>Conversations reçues dans Guesty</h3><p class="fine">Ouvrez une conversation pour voir les messages. Les propositions IA apparaissent en dessous.</p></div></div><div class="received-grid">${threads.map(thread=>{
    const guests=thread.posts.filter(p=>p.sender==='guest'), last=guests.at(-1)||thread.posts.at(-1);
    return `<button class="received-card" data-inbox-thread="${esc(thread.conversation_id)}"><div><strong>${esc(thread.guest_name)}</strong><span>${esc(thread.property_name)}</span></div><div>${translatedMessage(last?.body||'Aucun message texte')}</div><small>${previews.has(thread.conversation_id)?'Proposition IA disponible':guests.length?'Message voyageur reçu':'Échanges hôte · consulter'} · ${last?.created_at?esc(new Date(last.created_at).toLocaleString('fr-FR',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})):''}</small></button>`;
  }).join('')}</div></section>`:'';
}

document.addEventListener('click',async event=>{
  const el=event.target.closest('button');if(!el)return;
  try{
    if(el.id==='sync-inbox'){
      el.disabled=true;el.textContent='Lecture et préparation en cours…';
      const result=await api('/observation/sync',{method:'POST'});
      await loadFeed();toast(result.error||`${result.imported||0} conversation(s) lue(s), ${result.generated||0} proposition(s) préparée(s).`);
    }
    if(el.dataset.observeEnabled){
      el.disabled=true;
      await api('/settings/observation',{method:'POST',body:{enabled:el.dataset.observeEnabled==='true'}});
      await loadInbox();
    }
    if(el.dataset.inboxThread){
      const thread=receivedThreads.find(t=>t.conversation_id===el.dataset.inboxThread);
      if(!thread)return;
      openDialog(thread.guest_name,`<p class="muted">${esc(thread.property_name)} · Conversation Guesty</p><div class="identity-callout"><p>Pour utiliser les accès et les équipements du bon logement, vérifiez son association Guesty une seule fois.</p><button class="secondary" data-verify-identity="${esc(thread.conversation_id)}">Vérifier le logement</button></div><div class="thread-messages">${thread.posts.map(post=>`<article class="thread-message ${post.sender==='guest'?'incoming':'outgoing'}"><small>${post.sender==='guest'?'Voyageur':post.sender==='user'?'Hôte / équipe':'Auteur à vérifier'} · ${post.created_at?esc(new Date(post.created_at).toLocaleString('fr-FR')):''}</small><div>${translatedMessage(post.body)}</div></article>`).join('')}</div>`);
    }
  }catch(exc){toast(exc.message);el.disabled=false;}
});
setInterval(()=>{if(state.identity&&state.page==='feed'&&!$('#sheet').open)loadFeed().catch(()=>{});},20000);

document.addEventListener('click',async event=>{
  const el=event.target.closest('button');if(!el)return;
  try{
    if(el.dataset.verifyIdentity){
      el.disabled=true;el.textContent='Vérification dans Guesty…';
      const data=await api('/inbox/'+el.dataset.verifyIdentity+'/identity',{method:'POST'});
      openDialog('Confirmer le bon logement',`<p>Les données fraîches de Guesty relient cette réservation au logement suivant :</p><div class="upload-destination"><span>⌂</span><div><strong>${esc(data.property_name)}</strong><small>${esc(data.guesty_name||'')}</small><p>${esc(data.address)}</p></div></div><p class="fine">Cette confirmation enregistre ses identifiants d’unité, sans modifier Guesty et sans envoyer de message. Les anciennes propositions restent inchangées.</p><details><summary>Voir les identifiants vérifiés</summary><code>Listing : ${esc(data.listing_id)}</code>${data.pairs.map(pair=>`<p><code>${esc(pair.kind)} : ${esc(pair.external_id)}</code></p>`).join('')}</details><div class="result-actions"><button class="primary" data-confirm-identity="${esc(data.change_id)}">Oui, c’est le bon logement</button><button class="secondary" data-open-page="feed">Revenir aux messages</button></div>`);
    }
    if(el.dataset.confirmIdentity){
      el.disabled=true;
      const result=await api('/identity/'+el.dataset.confirmIdentity+'/confirm',{method:'POST',body:{accept:true}});
      $('#sheet').close();toast(result.message);await loadFeed();
    }
  }catch(exc){toast(exc.message);el.disabled=false;}
});

async function teachProperty(id){
  const result=await api('/properties/'+id+'/teach',{method:'POST'});
  $('#sheet').close();await navigate('chat');appendMessage('assistant',result);$('#chat-input').focus();
}
document.addEventListener('click',async event=>{
  const el=event.target.closest('button');if(!el)return;
  try{
    if(el.dataset.teachProperty)await teachProperty(el.dataset.teachProperty);
    if(el.id==='sync-properties'){el.disabled=true;await api('/properties/sync',{method:'POST'});await loadProperties();el.disabled=false;}
  }catch(error){toast(error.message);el.disabled=false;}
});
document.addEventListener('submit',async event=>{
  const form=event.target;
  if(form.id==='home-agent-form'){
    event.preventDefault();const message=$('#home-agent-input').value;await navigate('chat');$('#chat-input').value=message;$('#chat-form').requestSubmit();
  }

});
