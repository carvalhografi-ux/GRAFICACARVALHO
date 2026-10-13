(function(){
'use strict';
function init(){
 const d=document.getElementById('printDialog');
 if(!d || !document.getElementById('printOpen'))return;
 const $=id=>document.getElementById(id);
 const modes=d.querySelector('.print-options');
 if(!modes.querySelector('input[value="a5"]')){
  const lbl=document.createElement('label');lbl.className='print-option';
  lbl.innerHTML='<input type="radio" name="printMode" value="a5"><span><strong>Uma OS · A5</strong><small>148 × 210 mm, retrato</small></span>';
  modes.appendChild(lbl);
 }
 const container=document.createElement('div');
 container.style.cssText='background:#f3f6fc;border:1px solid #d4ddeb;border-radius:12px;padding:14px;margin:15px 0';
 container.innerHTML='<label for="gcPrinter" style="font-weight:800">Impressão automática · escolha a impressora</label>'
 +'<div style="display:grid;grid-template-columns:2fr 1fr;gap:9px;margin:9px 0">'
 +'<select id="gcPrinter" style="padding:9px;min-width:0;width:100%"><option value="printer1">Impressora 1 · IPv6/WSD</option><option value="printer2">Impressora 2 · 192.168.0.105</option></select>'
 +'<select id="gcCopies" aria-label="Cópias" style="padding:9px;min-width:0;width:100%"><option value="1">1 cópia</option><option value="2">2 cópias</option><option value="3">3 cópias</option><option value="4">4 cópias</option><option value="5">5 cópias</option></select></div>'
 +'<p id="gcAgentStatus" role="status" style="font-size:12px;margin:10px 0;color:#5c647b">Verificando agente Windows...</p>'
 +'<div id="gcAgentAdmin" hidden><button class="btn" type="button" id="gcAgentKey">⚙ Gerar chave para conectar o computador</button>'
 +'<div id="gcAgentKeyArea" hidden style="margin-top:8px"><label for="gcAgentToken" style="font-size:12px">Chave (exibida uma vez)</label>'
 +'<textarea id="gcAgentToken" rows="3" readonly style="display:block;width:100%;margin:6px 0"></textarea>'
 +'<button type="button" class="btn" id="gcAgentCopy">Copiar chave</button></div></div>';
 const note=d.querySelector('.print-note');note.parentNode.insertBefore(container,note);
 const btn=document.createElement('button');btn.id='gcAgentPrint';btn.type='button';
 btn.className='btn primary';btn.disabled=true;btn.textContent='🖨 Imprimir automaticamente';
 $('printOpen').parentNode.insertBefore(btn,$('printOpen'));
 const before=window.chosenPrintUrl;
 if(typeof before==='function')window.chosenPrintUrl=function(inline){
  if(printMode()!=='a5')return before(inline);
  const id=$('printOrder1').value;if(!id)throw Error('Selecione uma ordem de serviço.');
  const q=new URLSearchParams({mode:'a5',id1:id});if(inline)q.set('visualizar','1');
  return '/api/print/os?'+q.toString();
 };
 async function refresh(){
  try{
   const [auth,state]=await Promise.all([GC_AUTH_READY,api('/api/print/status')]);
   $('gcAgentAdmin').hidden=auth.user.role!=='admin';
   $('gcPrinter').options[0].textContent='Impressora 1 · '+(state.printers.printer1||'IPv6/WSD');
   $('gcPrinter').options[1].textContent='Impressora 2 · '+(state.printers.printer2||'192.168.0.105');
   btn.disabled=!state.online;
   $('gcAgentStatus').textContent=state.online?'✓ Computador conectado. Impressão direta disponível.':
    '⚠ Agente Windows desconectado. No PC da gráfica, execute INICIAR_IMPRESSAO.bat.';
  }catch(e){btn.disabled=true;$('gcAgentStatus').textContent='Não foi possível verificar agente: '+e.message}
 }
 document.addEventListener('click',function(event){
  const x=event.target.closest('button');
  if(x&&(x.id==='btnPrint'||x.dataset.action==='print'))setTimeout(refresh,400);
 },true);
 $('gcAgentKey').onclick=async function(){
  if(!confirm('Uma nova chave invalida a chave instalada anteriormente. Continuar?'))return;
  try{
   const result=await api('/api/print/agent/key',{method:'POST',
     headers:{'Content-Type':'application/json'},body:'{}'});
   $('gcAgentToken').value=result.token;$('gcAgentKeyArea').hidden=false;
   notify('Copie a chave exclusivamente para o agente Windows.');await refresh();
  }catch(e){notify(e.message)}
 };
 $('gcAgentCopy').onclick=async function(){
  try{await navigator.clipboard.writeText($('gcAgentToken').value);notify('Chave copiada.')}
  catch(e){$('gcAgentToken').select();notify('Selecione a chave e copie manualmente.')}
 };
 btn.onclick=async function(){
  try{
   const first=$('printOrder1').value,mode=printMode(),second=$('printOrder2').value;
   if(!first)throw Error('Escolha uma OS.');
   if(mode==='dupla'&&(!second||first===second))throw Error('Escolha duas OS diferentes.');
   btn.disabled=true;btn.textContent='Enviando...';
   await api('/api/print/jobs',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({printer:$('gcPrinter').value,mode:mode,id1:first,id2:second,copies:Number($('gcCopies').value)})});
   d.close();notify('OS enviada à impressora. Confira a fila do Windows.');
  }catch(e){notify('Impressão não enviada: '+e.message)}
  finally{btn.textContent='🖨 Imprimir automaticamente';await refresh()}
 };
 modes.addEventListener('change',function(){
  if(printMode()==='a5'){$('printPreviewTitle').textContent='A5 vertical · 1 OS';}
 });
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);
else init();
})();