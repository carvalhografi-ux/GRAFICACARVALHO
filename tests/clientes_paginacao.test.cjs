const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
for(const file of ['public/index.html']){
 const html=readFileSync(file,'utf8');
 assert.match(html,/id="clientPagination"/);
 assert.match(html,/CLIENTS_PER_PAGE=12/);
 assert.match(html,/clientPage=1;\s*clearTimeout\(clientSearchTimer\)/);
 const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)].map(x=>x[1]).filter(Boolean);
 for(const [i,js] of scripts.entries())new vm.Script(js,{filename:file+' inline script '+i});
 const start=html.indexOf('function clientPageSlots(');
 const end=html.indexOf('async function openClientModal(',start);
 assert.ok(start>0&&end>start);
 const fragment=html.slice(start,end);
 const harness=`
 const assert=globalAssert;
 const CLIENTS_PER_PAGE=12;
 let clientPage=1;
 let clientList=Array.from({length:665},(_,i)=>({id:String(i),name:'Cliente '+i,email:'',address:'',phone:'',document:'',orders_count:0}));
 let filteredClientList=clientList;
 const nodes={clientRows:{innerHTML:''},clientsTotal:{textContent:''},clientPagination:{hidden:false,innerHTML:''}};
 function $(x){return nodes[x]}
 function esc(x){return String(x)}
 ${fragment}
 assert.equal(clientPageSlots(1,56).join(','),'1,2,3,…,56');
 assert.equal(clientPageSlots(28,56).join(','),'1,…,26,27,28,29,30,…,56');
 assert.equal(clientPageSlots(56,56).join(','),'1,…,54,55,56');
 renderClientPage();
 assert.equal((nodes.clientRows.innerHTML.match(/<tr>/g)||[]).length,12);
 assert.match(nodes.clientsTotal.textContent,/Exibindo 1–12 de 665 clientes/);
 assert.match(nodes.clientsTotal.textContent,/Página 1 de 56/);
 assert.match(nodes.clientPagination.innerHTML,/aria-current="page"/);
 clientPage=56;renderClientPage();
 assert.equal((nodes.clientRows.innerHTML.match(/<tr>/g)||[]).length,5);
 assert.match(nodes.clientsTotal.textContent,/Exibindo 661–665 de 665 clientes/);
 filteredClientList=clientList.slice(0,9);
 clientPage=56;renderClientPage();
 assert.equal(clientPage,1);
 assert.equal((nodes.clientRows.innerHTML.match(/<tr>/g)||[]).length,9);
 assert.equal(nodes.clientPagination.hidden,true);
 filteredClientList=[];renderClientPage();
 assert.equal((nodes.clientRows.innerHTML.match(/<tr>/g)||[]).length,1);
 assert.match(nodes.clientsTotal.textContent,/Exibindo 0–0 de 0 clientes/);
 console.log('PASSOU: páginas numeradas, 12 por aba, busca, último lote, vazio');
 `;
 vm.runInNewContext(harness,{globalAssert:assert,console});
}
