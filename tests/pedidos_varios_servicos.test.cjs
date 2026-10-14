const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=readFileSync('public/index.html','utf8');
assert.match(html,/id="addService"/);
assert.match(html,/id="serviceItems"/);
assert.match(html,/id="fService"/);
assert.match(html,/resetOrderServices\(s\?\.service\|\|''\)/);
assert.match(html,/service:serviceDescription/);
assert.match(html,/id="clientPagination"/);
const scripts=[...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/gi)]
  .map(m=>m[1]).filter(Boolean);
for(const [i,code] of scripts.entries()){
  new vm.Script(code,{filename:'inline '+i});
}
const start=html.indexOf('const ORDER_SERVICE_LIMIT=8;');
const end=html.indexOf("$('addService').addEventListener",start);
assert.ok(start>0&&end>start);
const script=html.slice(start,end);
const first={value:''};
let inputs=[first];
const widgets={
  serviceItems:{querySelectorAll(q){return q==='.service-entry-input'?inputs:[]}},
  fService:first
};
const context={$:id=>widgets[id],document:{createElement(){throw Error('unexpected element')}},console};
vm.createContext(context);
vm.runInContext(script,context);
const single=context.readOrderServiceList('Banner 100 x 70');
assert.deepEqual([...single],['Banner 100 x 70']);
const existing=context.readOrderServiceList('1. Adesivo 30x20\n2. Banner 120x90\n3. Placa ACM');
assert.deepEqual([...existing],['Adesivo 30x20','Banner 120x90','Placa ACM']);
inputs=['Banner 120 x 90','Adesivo 30 x 20','Placa ACM'].map(value=>({value}));
assert.equal(context.collectOrderServices(),'1. Banner 120 x 90\n2. Adesivo 30 x 20\n3. Placa ACM');
inputs=[{value:'Somente um serviço'}];
assert.equal(context.collectOrderServices(),'Somente um serviço');
inputs=[{value:' '},{value:'Descrição'}];
assert.throws(()=>context.collectOrderServices(),/Preencha a descrição/);
inputs=Array.from({length:9},(_,i)=>({value:'Serviço '+i}));
assert.throws(()=>context.collectOrderServices(),/até 8/);
inputs=[{value:'a'.repeat(301)}];
assert.throws(()=>context.collectOrderServices(),/máximo de 300/);
console.log('OK: JS válido, pedidos antigos, vários serviços e validações.');
