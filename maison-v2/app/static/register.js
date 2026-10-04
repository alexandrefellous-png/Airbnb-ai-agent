'use strict';
document.querySelector('#register-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.currentTarget, button=form.querySelector('button'), error=document.querySelector('#register-error');
  const values=Object.fromEntries(new FormData(form));
  error.textContent='';
  if(values.password!==values.confirm_password){error.textContent='Les deux mots de passe doivent être identiques.';return;}
  delete values.confirm_password;button.disabled=true;
  try{
    const response=await fetch('/admin/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});
    const data=await response.json();
    if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:'Vérifiez votre email et votre mot de passe.');
    location.assign('/admin');
  }catch(exc){error.textContent=exc.message;}finally{button.disabled=false;}
});
