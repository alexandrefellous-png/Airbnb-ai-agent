'use strict';
(async()=>{
  const query=new URLSearchParams(location.search), result=document.querySelector('#drive-result');
  const body={code:query.get('code'),state:query.get('state')};
  const declined=query.has('error');history.replaceState(null,'','/admin/drive/callback');
  try{
    if(declined)throw Error('Autorisation Google annulée. Vous pouvez recommencer depuis votre espace.');
    if(!body.code||!body.state)throw Error('Connexion incomplète. Recommencez depuis votre espace.');
    const session=await fetch('/admin/session').then(r=>{if(!r.ok)throw Error('Reconnectez-vous à Maison puis recommencez la connexion Google.');return r.json();});
    const response=await fetch('/admin/settings/drive/finish',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':session.csrf},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok)throw Error(data.detail||'Connexion impossible.');
    result.textContent=data.message;setTimeout(()=>location.assign('/admin#connections'),1000);
  }catch(exc){result.textContent=exc.message;}
})();
