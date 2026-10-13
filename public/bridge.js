(()=>{
  'use strict';
  const tokenKey='gc_render_session_v1';
  const nativeFetch=window.fetch.bind(window);
  const base=String(window.GRAFICA_API_URL||'').replace(/\/$/,'');
  let ready=false;
  try { let url=new URL(base);ready=url.protocol==='https:' && url.origin===base && !url.hostname.includes('example') }catch(e){}
  function failNotConfigured(){throw new Error('Conexão com o servidor não configurada. Informe a URL do Render ao administrador.');}
  function signOut(){sessionStorage.removeItem(tokenKey);}
  const loginHref='/login.html';
  async function routedFetch(input,options={}){
    const url=typeof input==='string'?input:input instanceof URL?input.href:input?.url;
    if(!url||!url.startsWith('/api/'))return nativeFetch(input,options);
    if(!ready)failNotConfigured();
    const headers=new Headers(options.headers || (input instanceof Request?input.headers:undefined));
    headers.delete('Cookie');
    const token=sessionStorage.getItem(tokenKey);
    if(token)headers.set('Authorization','Bearer '+token);
    const realPath=url==='/api/auth/login'?'/api/auth/token-login':url;
    const response=await nativeFetch(base+realPath,{...options,headers,credentials:'omit',cache:'no-store',redirect:'error'});
    if(realPath==='/api/auth/token-login'&&response.ok){
      const payload=await response.clone().json();
      if(payload?.access_token)sessionStorage.setItem(tokenKey,payload.access_token);
    }
    if(realPath==='/api/auth/logout' || (realPath==='/api/auth/password' && response.ok))signOut();
    if(response.status===401 && realPath!=='/api/auth/token-login'){
      signOut();
      if(location.pathname!==loginHref)location.replace(loginHref);
    }
    return response;
  }
  window.fetch=routedFetch;
  window.gcHasBackend=ready;
  window.gcSignOut=signOut;
  window.gcOpenProtected=async (path,show=false)=>{
    // Abra a janela antes do await para respeitar bloqueadores de pop-up.
    const tab=show?window.open('about:blank','_blank'):null;
    let r;
    try{r=await routedFetch(path)}catch(error){if(tab)tab.close();throw error}
    if(!r.ok){if(tab)tab.close();throw new Error('Não foi possível abrir o arquivo (HTTP '+r.status+').')}
    const b=await r.blob(),url=URL.createObjectURL(b);
    if(show){if(tab){tab.location.href=url}else alert('Permita pop-ups para visualizar o PDF.');setTimeout(()=>URL.revokeObjectURL(url),120000);return;}
    let filename='arquivo';const disp=r.headers.get('Content-Disposition')||'';
    const utf=disp.match(/filename\*=UTF-8''([^;]+)/i),basic=disp.match(/filename="?([^";]+)"?/i);
    if(utf){try{filename=decodeURIComponent(utf[1])}catch(e){}}else if(basic)filename=basic[1];
    const a=document.createElement('a');a.href=url;a.download=filename;document.body.appendChild(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
  };
  let previews=new Map();
  async function processPreviews(){
    for(const im of document.querySelectorAll('img[src^="/api/"]')){
      const path=im.getAttribute('src');if(!path)continue;
      im.removeAttribute('src');
      try {
        let data=previews.get(path);
        if(!data){const r=await routedFetch(path);if(!r.ok)throw Error('prévia');data=URL.createObjectURL(await r.blob());previews.set(path,data)}
        im.src=data;
      }catch(e){im.alt='Prévia indisponível';}
    }
  }
  document.addEventListener('DOMContentLoaded',()=>{
    if(!ready){
      const notice=document.createElement('div');
      notice.style.cssText='position:fixed;bottom:16px;left:16px;z-index:9999;background:#fff1df;color:#7b4706;border:1px solid #f3d6a7;border-radius:12px;padding:13px 16px;max-width:430px;font:13px Arial;box-shadow:0 8px 24px #0002';
      notice.textContent='A interface Wix está pronta. Falta configurar a URL HTTPS do servidor Python no Render.';
      document.body.appendChild(notice);
    }
    document.addEventListener('click',ev=>{
      const a=ev.target.closest('a[href^="/api/"]');if(!a)return;
      ev.preventDefault();ev.stopPropagation();window.gcOpenProtected(a.getAttribute('href')).catch(e=>alert(e.message));
    },true);
    const obs=new MutationObserver(()=>{processPreviews()});obs.observe(document.body,{childList:true,subtree:true});
    processPreviews();
  });
})();
