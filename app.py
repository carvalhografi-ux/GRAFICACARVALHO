"""Gráfica Carvalho — API Python no Render Free com dados persistidos em PostgreSQL Neon."""
from __future__ import annotations
import base64
import security
import print_queue
from datetime import date, datetime
from email.parser import BytesParser
from email import policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import database
import struct
from threading import Timer
from urllib.parse import parse_qs, quote, unquote, urlparse
import uuid
import webbrowser
import zlib
import textwrap

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / 'static'
LOGO = STATIC/'logo_carvalho.png'
STATUS = {'Pendente','Em produção','Aguardando cliente','Concluído','Cancelado'}
PRIORITIES = {'Normal','Alta','Urgente'}
MAX_FILE = 30*1024*1024
# Proteção de memória para o servidor Render Free (envios de até 40 MB em arquivos).
MAX_UPLOAD_BODY = 42*1024*1024

class AppError(Exception):
    def __init__(self, status:int, message:str):
        super().__init__(message)
        self.status=status
        self.detail=message

connect = database.connect

def init_db():
    with connect() as db:
        db.execute('''CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY, code TEXT NOT NULL DEFAULT '', client TEXT NOT NULL,
            phone TEXT NOT NULL DEFAULT '', service TEXT NOT NULL, quantity INTEGER NOT NULL DEFAULT 1,
            entry_date TEXT NOT NULL, due_date TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'Normal',
            status TEXT NOT NULL DEFAULT 'Pendente', total DOUBLE PRECISION NOT NULL DEFAULT 0,
            received DOUBLE PRECISION NOT NULL DEFAULT 0, notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY, order_id TEXT NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            filename TEXT NOT NULL, content_type TEXT NOT NULL DEFAULT 'application/octet-stream',
            size INTEGER NOT NULL, data BYTEA NOT NULL, created_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS ix_orders_due ON orders(due_date,status)')
        db.execute('CREATE INDEX IF NOT EXISTS ix_attachments_order ON attachments(order_id)')
        db.execute('''CREATE TABLE IF NOT EXISTS clients (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, phone TEXT NOT NULL DEFAULT '',
          email TEXT NOT NULL DEFAULT '', document TEXT NOT NULL DEFAULT '',
          address TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS ix_clients_name ON clients(LOWER(name))')
        db.execute('ALTER TABLE orders ADD COLUMN IF NOT EXISTS client_id TEXT')
        db.execute('''CREATE TABLE IF NOT EXISTS order_counter (
          id INTEGER PRIMARY KEY CHECK (id = 1), last_num INTEGER NOT NULL DEFAULT 0)''')
        db.execute('INSERT INTO order_counter(id,last_num) VALUES (1,0) ON CONFLICT (id) DO NOTHING')
        # Migração sem apagar pedidos: preserva códigos antigos únicos.
        # Códigos antigos vazios ou repetidos recebem um novo número GC.
        normalize_existing_codes(db)
        migrate_order_clients(db)
        db.execute('CREATE UNIQUE INDEX IF NOT EXISTS ux_orders_code ON orders(LOWER(code))')
    print_queue.setup()

CODE_RE=re.compile(r'^GC-(\d+)$',re.IGNORECASE)

def highwater(db):
    # Calcula o maior código sem reduzir o contador persistente.
    m=db.execute('SELECT last_num FROM order_counter WHERE id=1').fetchone()
    high=int(m[0]) if m else 0
    for row in db.execute('SELECT code FROM orders'):
        found=CODE_RE.fullmatch((row['code'] or '').strip())
        if found:high=max(high,int(found.group(1)))
    return high

def next_code(db):
    # Deve ser chamado apenas dentro de uma transação de escrita.
    num=int(db.execute('SELECT last_num FROM order_counter WHERE id=1 FOR UPDATE').fetchone()[0])
    while True:
        num+=1
        code=f'GC-{num:06d}'
        if not db.execute('SELECT 1 FROM orders WHERE LOWER(code)=LOWER(?)',(code,)).fetchone():break
    db.execute('UPDATE order_counter SET last_num=? WHERE id=1',(num,))
    return code

def normalize_existing_codes(db):
    maxnum=highwater(db)
    db.execute('UPDATE order_counter SET last_num=? WHERE id=1',(maxnum,))
    seen=set()
    for row in db.execute('SELECT id,code FROM orders ORDER BY created_at ASC,id ASC').fetchall():
        code=(row['code'] or '').strip()
        if not code or code.casefold() in seen:
            code=next_code(db)
            db.execute('UPDATE orders SET code=? WHERE id=?',(code,row['id']))
        elif code!=row['code']:
            db.execute('UPDATE orders SET code=? WHERE id=?',(code,row['id']))
        seen.add(code.casefold())

def migrate_order_clients(db):
    # Importa nomes antigos para o cadastro sem alterar pedidos.
    for o in db.execute('SELECT id,client,phone,client_id FROM orders ORDER BY created_at').fetchall():
        if o['client_id'] and db.execute('SELECT 1 FROM clients WHERE id=?',(o['client_id'],)).fetchone():continue
        found=db.execute('SELECT id FROM clients WHERE LOWER(name)=LOWER(?) AND phone=? LIMIT 1',(o['client'],o['phone'])).fetchone()
        if found:cid=found['id']
        else:
            cid=uuid.uuid4().hex;stamp=now()
            db.execute('INSERT INTO clients(id,name,phone,email,document,address,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                (cid,o['client'],o['phone'],'','','','',stamp,stamp))
        db.execute('UPDATE orders SET client_id=? WHERE id=?',(cid,o['id']))

def client_validation(payload):
    if not isinstance(payload,dict):raise AppError(422,'Dados do cliente inválidos.')
    values={}
    for k,maxlen in [('name',120),('phone',50),('email',150),('document',40),('address',300),('notes',3000)]:
        v=str(payload.get(k) or '').strip()
        if len(v)>maxlen or (k=='name' and not v):raise AppError(422,'Campo inválido: '+k)
        values[k]=v
    return values

def client_detail(db,cid):
    row=db.execute('SELECT * FROM clients WHERE id=?',(cid,)).fetchone()
    if not row:raise AppError(404,'Cliente não encontrado.')
    return dict(row)

def list_clients(search=''):
    with connect() as db:
        rows=[dict(x) for x in db.execute('''SELECT c.*,(SELECT COUNT(*) FROM orders o WHERE o.client_id=c.id) AS orders_count
         FROM clients c ORDER BY LOWER(name)''')]
    if search.strip():
        term=search.strip().casefold()
        rows=[r for r in rows if term in ' '.join(str(r[k]) for k in ('name','phone','email','document','address')).casefold()]
    return {'items':rows,'total':len(rows)}

def create_client(data):
    v=client_validation(data);cid=uuid.uuid4().hex;t=now()
    with connect() as db:
        db.execute('INSERT INTO clients(id,name,phone,email,document,address,notes,created_at,updated_at) VALUES (:id,:name,:phone,:email,:document,:address,:notes,:created_at,:updated_at)',{**v,'id':cid,'created_at':t,'updated_at':t})
        return client_detail(db,cid)

def update_client(cid,data):
    v=client_validation(data)
    with connect() as db:
        client_detail(db,cid)
        db.execute('''UPDATE clients SET name=:name,phone=:phone,email=:email,document=:document,
         address=:address,notes=:notes,updated_at=:updated_at WHERE id=:id''',{**v,'id':cid,'updated_at':now()})
        return client_detail(db,cid)

def delete_client(cid):
    with connect() as db:
        client_detail(db,cid)
        if db.execute('SELECT 1 FROM orders WHERE client_id=? LIMIT 1',(cid,)).fetchone() or db.execute('SELECT 1 FROM app_users WHERE client_id=? LIMIT 1',(cid,)).fetchone():
            raise AppError(409,'Cliente associado a pedidos ou acesso ao portal. Remova a associação primeiro.')
        db.execute('DELETE FROM clients WHERE id=?',(cid,))

def attach_order_client(db, data, payload):
    cid=str(payload.get('client_id') or '').strip()
    if cid:
        c=client_detail(db,cid)
        data['client_id']=cid
        data['client']=c['name']
        data['phone']=c['phone']
    else:
        # Compatível com integrações e backups anteriores.
        row=db.execute('SELECT id FROM clients WHERE LOWER(name)=LOWER(?) AND phone=? LIMIT 1',(data['client'],data['phone'])).fetchone()
        if row:cid=row['id']
        else:
            cid=uuid.uuid4().hex;t=now()
            db.execute("INSERT INTO clients(id,name,phone,email,document,address,notes,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (cid,data['client'],data['phone'],'','','','',t,t))
        data['client_id']=cid
    return data

def now(): return datetime.now().isoformat(timespec='seconds')
def today(): return date.today().isoformat()
def validate_day(v):
    try: return date.fromisoformat(v).isoformat()
    except (ValueError,TypeError): raise AppError(422,'Data inválida. Use AAAA-MM-DD.')
def payment(row):
    total=float(row['total']); received=float(row['received'])
    if total<=0: return 'Sem valor'
    if received>=total-.005: return 'Pago'
    if received>.005: return 'Parcial'
    return 'Não pago'
def record(row,count=None):
    d=dict(row); d['payment']=payment(d)
    d['balance']=round(max(0,float(d['total'])-float(d['received'])),2)
    if count is not None: d['attachments_count']=count
    return d

def validated(payload):
    if not isinstance(payload,dict): raise AppError(422,'Informe um pedido válido.')
    try:
        def get_text(key, maxlen, required=False, default=''):
            v=str(payload.get(key,default) if payload.get(key,default) is not None else '').strip()
            if len(v)>maxlen or (required and not v): raise ValueError('Campo inválido: '+key)
            return v
        p={
            'code':get_text('code',28),
            'client':get_text('client',100,True),
            'phone':get_text('phone',50),
            'service':get_text('service',3000,True),
            'quantity':int(payload.get('quantity',1)),
            'entry_date':validate_day(payload.get('entry_date')),
            'due_date':validate_day(payload.get('due_date')),
            'priority':get_text('priority',30,default='Normal'),
            'status':get_text('status',40,default='Pendente'),
            'total':round(float(payload.get('total',0)),2),
            'received':round(float(payload.get('received',0)),2),
            'notes':get_text('notes',12000)
        }
        if not 1<=p['quantity']<=1000000: raise ValueError('Quantidade inválida')
        if p['priority'] not in PRIORITIES or p['status'] not in STATUS: raise ValueError('Prioridade/situação inválida')
        if not(0<=p['received']<=p['total']<=1000000000): raise ValueError('Valores de pagamento inválidos')
        return p
    except (ValueError,TypeError,OverflowError) as exc: raise AppError(422,str(exc))

def fetch_order(db,oid):
    row=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
    if row is None: raise AppError(404,'Pedido não encontrado.')
    n=db.execute('SELECT COUNT(*) FROM attachments WHERE order_id=?',(oid,)).fetchone()[0]
    return record(row,n)

def orders(view='hoje',day=None,search=''):
    day=validate_day(day or today())
    if view not in ('hoje','todos','concluidos','pagos','parciais','naopagos'): raise AppError(422,'Filtro inválido.')
    with connect() as db:
        rows=db.execute('''SELECT o.*,(SELECT COUNT(*) FROM attachments a WHERE a.order_id=o.id)
             AS attachments_count FROM orders o ORDER BY o.due_date ASC, o.created_at DESC''').fetchall()
    arr=[record(r,r['attachments_count']) for r in rows]
    if view=='hoje': arr=[x for x in arr if x['due_date']<=day and x['status'] not in ('Concluído','Cancelado')]
    if view=='concluidos': arr=[x for x in arr if x['status']=='Concluído']
    if view in ('pagos','parciais','naopagos'):
        want={'pagos':'Pago','parciais':'Parcial','naopagos':'Não pago'}[view]
        arr=[x for x in arr if x['payment']==want]
    if search.strip():
        needle=search.casefold().strip()
        arr=[r for r in arr if needle in ' '.join((r['code'],r['client'],r['service'],r['phone'])).casefold()]
    return {'items':arr,'total':len(arr)}

def summary(day=None):
    day=validate_day(day or today())
    with connect() as db: rows=db.execute('SELECT status,due_date,total,received FROM orders').fetchall()
    pending=[x for x in rows if x['due_date']<=day and x['status'] not in ('Concluído','Cancelado')]
    return {'pending':len(pending), 'producing':sum(x['status']=='Em produção' for x in pending),
        'completed':sum(x['status']=='Concluído' for x in rows),
        'receivable':round(sum(max(0,x['total']-x['received']) for x in rows if x['status']!='Cancelado'),2),
        'counts':{'hoje':len(pending),'todos':len(rows),
            'concluidos':sum(x['status']=='Concluído' for x in rows),
            'pagos':sum(payment(x)=='Pago' for x in rows),
            'parciais':sum(payment(x)=='Parcial' for x in rows),
            'naopagos':sum(payment(x)=='Não pago' for x in rows)}}

def create_order(payload):
    data=validated(payload);oid=uuid.uuid4().hex;t=now()
    with connect() as db:
        db.execute('SELECT last_num FROM order_counter WHERE id=1 FOR UPDATE')
        data=attach_order_client(db,data,payload)
        data['code']=next_code(db)  # emitido na gravação, nunca confiamos no código enviado pelo navegador
        db.execute('''INSERT INTO orders (id,code,client,client_id,phone,service,quantity,entry_date,due_date,
               priority,status,total,received,notes,created_at,updated_at)
               VALUES (:id,:code,:client,:client_id,:phone,:service,:quantity,:entry_date,:due_date,
               :priority,:status,:total,:received,:notes,:created_at,:updated_at)''',
               {**data,'id':oid,'created_at':t,'updated_at':t})
        return fetch_order(db,oid)

def update_order(oid,payload):
    data=validated(payload)
    with connect() as db:
        current=fetch_order(db,oid)
        data=attach_order_client(db,data,payload)
        # O código é imutável, até se o navegador tentar modificá-lo.
        db.execute('''UPDATE orders SET client=:client,client_id=:client_id,phone=:phone,service=:service,
              quantity=:quantity,entry_date=:entry_date,due_date=:due_date,priority=:priority,
              status=:status,total=:total,received=:received,notes=:notes,updated_at=:updated_at WHERE id=:id''',
              {**data,'id':oid,'updated_at':now()})
        return fetch_order(db,oid)

def get_attachment(aid):
    with connect() as db: row=db.execute('SELECT * FROM attachments WHERE id=?',(aid,)).fetchone()
    if row is None: raise AppError(404,'Anexo não encontrado.')
    return row

def store_attachments(oid,files):
    if len(files)>35: raise AppError(413,'Máximo de 35 arquivos por envio.')
    saved=[];prepared=[]
    for filename,mime,data in files:
        if not data: continue
        if len(data)>MAX_FILE: raise AppError(413,'Arquivo excede 30 MB: '+filename)
        filename=Path(filename.replace('\\','/')).name[:220]
        mime=(mime or 'application/octet-stream')[:120]
        aid=uuid.uuid4().hex
        prepared.append((aid,oid,filename,mime,len(data),data,now()))
        saved.append({'id':aid,'filename':filename,'content_type':mime,'size':len(data)})
    with connect() as db:
        fetch_order(db,oid)
        db.executemany('''INSERT INTO attachments(id,order_id,filename,content_type,size,data,created_at)
               VALUES(?,?,?,?,?,?,?)''',prepared)
    return {'items':saved}

def backup():
    with connect() as db:
        rows=db.execute('SELECT * FROM orders ORDER BY created_at').fetchall()
        attachments=db.execute('SELECT * FROM attachments ORDER BY created_at').fetchall()
        clients=[dict(x) for x in db.execute('SELECT * FROM clients ORDER BY name')]
        last_order_number=highwater(db)
    services=[{'id':r['id'],'code':r['code'],'client':r['client'],'phone':r['phone'],
        'client_id':r['client_id'],'service':r['service'],'qty':r['quantity'],'entry':r['entry_date'],'due':r['due_date'],
        'priority':r['priority'],'status':r['status'],'total':r['total'],
        'received':r['received'],'notes':r['notes']} for r in rows]
    files=[{'id':r['id'],'serviceId':r['order_id'],'name':r['filename'],
        'type':r['content_type'],'size':r['size'],'created':r['created_at'],
        'base64':base64.b64encode(r['data']).decode('ascii')} for r in attachments]
    return json.dumps({'app':'grafica-carvalho-python-offline','exported':now(),
        'services':services,'attachments':files,'clients':clients,'last_order_number':last_order_number},ensure_ascii=False).encode('utf-8')

def restore(raw):
    try:
        doc=json.loads(raw)
        services=doc if isinstance(doc,list) else doc['services']
        attachments=[] if isinstance(doc,list) else doc.get('attachments',[])
        clients=[] if isinstance(doc,list) else doc.get('clients',[])
        backed_up_last_num=0 if isinstance(doc,list) else max(0,int(doc.get('last_order_number') or 0))
        if not isinstance(services,list) or not isinstance(attachments,list) or not isinstance(clients,list): raise ValueError('Estrutura do backup inválida')
        parsed_clients=[];client_ids=set()
        for c in clients:
            cv=client_validation(c)
            cid=str(c.get('id') or uuid.uuid4().hex)
            if cid in client_ids:raise ValueError('ID de cliente duplicado')
            client_ids.add(cid)
            parsed_clients.append({**cv,'id':cid,'created_at':str(c.get('created_at') or now()),'updated_at':now()})
        parsed=[];ids=set()
        for s in services:
            entry=validated({'code':s.get('code') or '', 'client':s['client'],'phone':s.get('phone') or '',
                'service':s['service'],'quantity':s.get('quantity',s.get('qty',1)),
                'entry_date':s.get('entry_date',s.get('entry')),'due_date':s.get('due_date',s.get('due')),
                'priority':s.get('priority','Normal'),'status':s.get('status','Pendente'),
                'total':s.get('total',0),'received':s.get('received',0),'notes':s.get('notes','')})
            oid=str(s.get('id') or uuid.uuid4().hex)
            if oid in ids: raise ValueError('ID de pedido duplicado')
            ids.add(oid)
            parsed.append({**entry,'id':oid,'client_id':str(s.get('client_id') or ''),'created_at':str(s.get('created_at') or now()),'updated_at':now()})
        files=[];file_ids=set()
        for a in attachments:
            oid=str(a.get('serviceId',a.get('order_id','')))
            if oid not in ids: raise ValueError('Anexo sem pedido correspondente')
            blob=base64.b64decode(a['base64'],validate=True)
            if len(blob)>MAX_FILE: raise ValueError('Anexo maior que 30 MB')
            fid=str(a.get('id') or uuid.uuid4().hex)
            if fid in file_ids: raise ValueError('ID de anexo duplicado')
            file_ids.add(fid)
            files.append((fid,oid,Path(str(a.get('name') or 'arquivo').replace('\\','/')).name[:220],
                str(a.get('type') or 'application/octet-stream')[:120],len(blob),blob,str(a.get('created') or now())))
    except (ValueError,KeyError,TypeError,AppError) as err:
        raise AppError(422,'Backup inválido: '+str(err))
    with connect() as db:
        # Os usuários não são apagados pelo backup. Recuse a restauração quando
        # ela deixaria contas do portal apontando para clientes inexistentes.
        linked={r['client_id'] for r in db.execute(
            "SELECT DISTINCT client_id FROM app_users WHERE role='client' AND client_id IS NOT NULL")}
        restored={c['id'] for c in parsed_clients}
        if not linked.issubset(restored):
            raise AppError(409, 'Este backup não contém todos os clientes associados a contas do portal. '
                              'Preserve os cadastros vinculados ou desative a vinculação antes de restaurar.')
        db.execute('SELECT last_num FROM order_counter WHERE id=1 FOR UPDATE')
        previous_high=max(highwater(db),backed_up_last_num)
        # Resolva conflitos ANTES do INSERT (o índice UNIQUE já existe no banco).
        max_num=previous_high
        for row in parsed:
            matched=CODE_RE.fullmatch((row['code'] or '').strip())
            if matched:max_num=max(max_num,int(matched.group(1)))
        seen_codes=set()
        for row in parsed:
            code=(row['code'] or '').strip()
            if not code or code.casefold() in seen_codes:
                while True:
                    max_num+=1;code=f'GC-{max_num:06d}'
                    if code.casefold() not in seen_codes:break
            row['code']=code
            seen_codes.add(code.casefold())
        db.execute('DELETE FROM attachments');db.execute('DELETE FROM orders');db.execute('DELETE FROM clients')
        db.executemany('''INSERT INTO clients(id,name,phone,email,document,address,notes,created_at,updated_at)
           VALUES (:id,:name,:phone,:email,:document,:address,:notes,:created_at,:updated_at)''',parsed_clients)
        db.executemany('''INSERT INTO orders(id,code,client,client_id,phone,service,quantity,entry_date,due_date,
           priority,status,total,received,notes,created_at,updated_at)
           VALUES(:id,:code,:client,:client_id,:phone,:service,:quantity,:entry_date,:due_date,
           :priority,:status,:total,:received,:notes,:created_at,:updated_at)''',parsed)
        # Normaliza backup antigo; remove código duplicado e gera apenas códigos únicos.
        # O contador não retrocede quando pedidos antigos são restaurados.
        db.execute('UPDATE order_counter SET last_num=? WHERE id=1',(previous_high,))
        normalize_existing_codes(db)
        migrate_order_clients(db)
        db.executemany('''INSERT INTO attachments(id,order_id,filename,content_type,size,data,created_at)
           VALUES(?,?,?,?,?,?,?)''',files)
    return {'ok':True,'orders':len(parsed),'attachments':len(files),'clients':db_client_count()}

def db_client_count():
    with connect() as db:return db.execute('SELECT COUNT(*) FROM clients').fetchone()[0]

# PDF A5, A4 retrato e duas OS A5 em A4 paisagem, sem bibliotecas externas.
MM = 72/25.4
PW,PH=148*MM,210*MM
A4_W,A4_H=210*MM,297*MM
A4_LAND_W,A4_LAND_H=297*MM,210*MM

def pdf_safe(s):
    # PDF WinAnsi suporta acentos em Português, sem depender de fontes externas.
    b=str(s).encode('cp1252','replace').replace(b'\\',b'\\\\').replace(b'(',b'\\(').replace(b')',b'\\)')
    return b.decode('latin1')

def pdf_wrap(s,width=72,size=9):
    # largura conservadora: previne overflow com fontes padrão PDF.
    max_chars=max(15,int(width/(size*.52)))
    lines=[]
    for para in str(s).splitlines() or ['']:
        lines.extend(textwrap.wrap(para,width=max_chars,break_long_words=True,break_on_hyphens=False) or [''])
    return lines

def png_to_rgb_alpha(raw):
    """PNG RGB/RGBA 8-bit não entrelaçado -> pixels RGB e alpha (para marca transparente)."""
    if raw[:8]!=b'\x89PNG\r\n\x1a\n': raise ValueError('PNG inválido')
    pos=8; pixels=b''; width=height=ctype=depth=interlace=None
    while pos<len(raw):
        length=struct.unpack('>I',raw[pos:pos+4])[0];kind=raw[pos+4:pos+8];data=raw[pos+8:pos+8+length];pos+=12+length
        if kind==b'IHDR': width,height,depth,ctype,_,_,interlace=struct.unpack('>IIBBBBB',data)
        if kind==b'IDAT':pixels+=data
        if kind==b'IEND':break
    if depth!=8 or ctype not in (2,6) or interlace:raise ValueError('PNG não compatível')
    channels=4 if ctype==6 else 3
    data=zlib.decompress(pixels)
    stride=width*channels
    rgb=bytearray(width*height*3);alpha=bytearray(width*height) if channels==4 else None
    prev=bytearray(stride);offset=0;j=0
    for y in range(height):
        flt=data[offset];offset+=1
        curr=bytearray(data[offset:offset+stride]);offset+=stride
        if len(curr)!=stride:raise ValueError('PNG incompleto')
        for i in range(stride):
            a=curr[i-channels] if i>=channels else 0
            b=prev[i];c=prev[i-channels] if i>=channels else 0
            if flt==1: v=a
            elif flt==2:v=b
            elif flt==3:v=(a+b)//2
            elif flt==4:
                p=a+b-c
                pa=abs(p-a);pb=abs(p-b);pc=abs(p-c)
                v=a if pa<=pb and pa<=pc else b if pb<=pc else c
            elif flt==0:v=0
            else:raise ValueError('Filtro PNG desconhecido')
            curr[i]=(curr[i]+v)&255
        for x in range(width):
            k=x*channels
            rgb[j*3:j*3+3]=curr[k:k+3]
            if alpha is not None:alpha[j]=curr[k+3]
            j+=1
        prev=curr
    return width,height,bytes(rgb),bytes(alpha) if alpha is not None else None

class PDF:
    def __init__(self): self.objects=[None];self.pages=[];self.resources={}
    def add(self,blob):
        self.objects.append(blob);return len(self.objects)-1
    def stream(self,dic,blob):
        return self.add(('<< '+dic+' /Length '+str(len(blob))+' >>\nstream\n').encode()+blob+b'\nendstream')
    def build(self,page_commands,img=None,page_w=PW,page_h=PH,extra_images=()):
        catalog=self.add(b'')
        pages=self.add(b'')
        font=self.add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>')
        fontb=self.add(b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>')
        image_entry=''
        if img:
            width,height,rgb,alpha=img
            smask=''
            if alpha:
                aid=self.stream(f'/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceGray /BitsPerComponent 8 /Filter /FlateDecode',zlib.compress(alpha,7))
                smask=f' /SMask {aid} 0 R'
            iid=self.stream(f'/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode{smask}',zlib.compress(rgb,7))
            image_entry=f' /Logo {iid} 0 R'
        for i,photo in enumerate(extra_images):
            width,height=photo['width'],photo['height']
            col='/DeviceGray' if photo['gray'] else '/DeviceRGB'
            iid=self.stream(f'/Type /XObject /Subtype /Image /Width {width} /Height {height} /ColorSpace {col} /BitsPerComponent 8 /Filter /DCTDecode',photo['data'])
            image_entry+=f' /Foto{i} {iid} 0 R'
        xobjects=f' /XObject <<{image_entry} >>' if image_entry else ''
        kids=[]
        for commands in page_commands:
            blob='\n'.join(commands).encode('latin1')
            content_id=self.stream('/Filter /FlateDecode',zlib.compress(blob,7))
            page_id=self.add(f'<< /Type /Page /Parent {pages} 0 R /MediaBox [0 0 {page_w:.4f} {page_h:.4f}] /Resources << /Font << /F1 {font} 0 R /F2 {fontb} 0 R >>{xobjects} >> /Contents {content_id} 0 R >>'.encode())
            kids.append(page_id)
        self.objects[pages]=f'<< /Type /Pages /Kids [{" ".join(str(x)+" 0 R" for x in kids)}] /Count {len(kids)} >>'.encode()
        self.objects[catalog]=f'<< /Type /Catalog /Pages {pages} 0 R >>'.encode()
        out=bytearray(b'%PDF-1.4\n%\xe2\xe3\xcf\xd3\n'); offsets=[0]
        for n,obj in enumerate(self.objects[1:],1):
            offsets.append(len(out));out.extend(f'{n} 0 obj\n'.encode()+obj+b'\nendobj\n')
        xref=len(out)
        out.extend(f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode())
        for p in offsets[1:]:out.extend(f'{p:010d} 00000 n \n'.encode())
        out.extend(f'trailer\n<< /Size {len(offsets)} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
        return bytes(out)


# Formatos raster suportados para miniaturas dos anexos nas folhas adicionais.
# Os originais permanecem intactos no banco e nos downloads do pedido.
PHOTO_MIMES = ('image/jpeg','image/png','image/webp','image/gif','image/bmp')
PHOTO_LIMIT_PER_OS = 12

def load_os_photos(oid, color='color'):
    """Carrega até 12 fotos por OS, sem trazer arquivos CDR/PDF nem os originais enormes.
    Cada foto gera um JPEG redimensionado, com metadados EXIF removidos."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    Image.MAX_IMAGE_PIXELS = 36_000_000
    photos=[]
    with connect() as db:
        rows=db.execute("""SELECT id,filename,size FROM attachments
            WHERE order_id=? AND LOWER(content_type) IN (?,?,?,?,?)
            ORDER BY created_at,id LIMIT ?""",
            (oid,*PHOTO_MIMES,PHOTO_LIMIT_PER_OS)).fetchall()
        for row in rows:
            if row['size']>20*1024*1024:continue
            stored=db.execute('SELECT data FROM attachments WHERE id=?',(row['id'],)).fetchone()
            if not stored:continue
            try:
                with Image.open(io.BytesIO(bytes(stored['data']))) as original:
                    if original.width*original.height>36_000_000:
                        continue
                    original.seek(0)  # GIF animado: apenas primeiro quadro.
                    pic=ImageOps.exif_transpose(original)
                    pic.thumbnail((1400,1400),Image.Resampling.LANCZOS)
                    if 'A' in pic.getbands() or ('transparency' in pic.info):
                        rgba=pic.convert('RGBA')
                        back=Image.new('RGB',rgba.size,'white')
                        back.paste(rgba,mask=rgba.getchannel('A'))
                        pic=back
                    pic=pic.convert('L' if color=='bw' else 'RGB')
                    out=io.BytesIO()
                    pic.save(out,format='JPEG',quality=78,optimize=True)
                    photos.append({'name':str(row['filename'])[:120],
                        'width':pic.width,'height':pic.height,
                        'gray':color=='bw','data':out.getvalue()})
                    break  # A OS exibe somente a primeira imagem válida.
            except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError):
                continue
    return photos

def service_lines_for_os(value,width_pt,size):
    """Formata descrições em itens numerados mantendo cada item visível na OS.
    Descrições muito extensas são abreviadas para caber numa única folha."""
    chunks=[x.strip() for x in str(value or '').splitlines() if x.strip()]
    numbered=len(chunks)>1 and all(re.match(r'^\\d+[.)]\\s+\\S',x) for x in chunks)
    if not numbered:
        return pdf_wrap(value,width_pt,size),False
    max_chars=max(15,int(width_pt/(size*.52)))
    shortened=[x if len(x)<=max_chars else x[:max_chars-3].rstrip()+'...' for x in chunks]
    return shortened,True

def make_os_pages(order, footer_label="A5 (148 x 210 mm)", color='color', photos=(), photo_offset=0):
    INK='#2c316f';MUT='#6b7393';FRAME='#e0e4f0';PURP='#34378e';WHITE='#ffffff'
    def rgb(h):
        h=h.lstrip('#')
        channels=[int(h[i:i+2],16) for i in (0,2,4)]
        if color=='bw':
            g=round(.299*channels[0]+.587*channels[1]+.114*channels[2])
            channels=[g,g,g]
        return ' '.join(f'{v/255:.3f}' for v in channels)
    def rect(cmd,x,y,w,h,fill=WHITE,stroke=None):
        xx=x*MM; yy=PH-(y+h)*MM
        if fill:cmd.append(f'{rgb(fill)} rg {xx:.2f} {yy:.2f} {w*MM:.2f} {h*MM:.2f} re f')
        if stroke:cmd.append(f'{rgb(stroke)} RG .6 w {xx:.2f} {yy:.2f} {w*MM:.2f} {h*MM:.2f} re S')
    def text(cmd,s,x,y,size=9,color=INK,bold=False):
        txt=pdf_safe(s)
        cmd.append(f'{rgb(color)} rg BT /F{2 if bold else 1} {size:.2f} Tf 1 0 0 1 {x*MM:.2f} {PH-y*MM:.2f} Tm ({txt}) Tj ET')
    def label(c,s,x,y):text(c,s.upper(),x,y,6.5,MUT,True)
    def header(c,sub):
        rect(c,0,0,148,42,PURP)
        for i,clr in enumerate(('#05aee6','#ee2a84','#e8d329','#5dc34d')):rect(c,37*i,40.5,37,1.5,clr)
        if png_logo is not None:
            # logo 112 x 26 mm, proporcional; com transparência real
            iw,ih=png_logo[:2];mw=min(110, 26*iw/ih);mh=mw*ih/iw
            x,y=12,6
            c.append(f'q {mw*MM:.2f} 0 0 {mh*MM:.2f} {x*MM:.2f} {PH-(y+mh)*MM:.2f} cm /Logo Do Q')
        else:text(c,'GRÁFICA CARVALHO',12,23,16,WHITE,True)
        text(c,sub,12,35,9,WHITE,True)
    def panel(c,y,h):rect(c,10,y,128,h,WHITE,FRAME)
    try:png_logo=png_to_rgb_alpha(LOGO.read_bytes()) if LOGO.exists() else None
    except (ValueError,zlib.error,struct.error):png_logo=None
    if color=='bw' and png_logo is not None:
        iw,ih,rgbbytes,alpha=png_logo
        gray=bytearray(len(rgbbytes))
        for k in range(0,len(rgbbytes),3):
            v=round(.299*rgbbytes[k]+.587*rgbbytes[k+1]+.114*rgbbytes[k+2])
            gray[k:k+3]=bytes((v,v,v))
        png_logo=(iw,ih,bytes(gray),alpha)
    c=[]; header(c,'ORDEM DE SERVIÇO')
    panel(c,45,17)
    label(c,'Nº DA OS',14,51);text(c,order['code'] or order['id'][:9].upper(),14,58,9,INK,True)
    label(c,'PEDIDO',61,51);text(c,format_date(order['entry_date']),61,58,8.8,INK,True)
    label(c,'ENTREGA',100,51);text(c,format_date(order['due_date']),100,58,8.8,INK,True)
    panel(c,65,23)
    label(c,'CLIENTE',14,71)
    name_lines=pdf_wrap(order['client'],120*MM,11)
    text(c,name_lines[0],14,78,10.5,INK,True)
    label(c,'TELEFONE / WHATSAPP',14,84);text(c,order['phone'] or 'Não informado',53,84,8)
    photo=photos[0] if photos else None
    if photo:
        rect(c,10,91,76,68,WHITE,FRAME)
        rect(c,89,91,49,68,WHITE,FRAME)
        label(c,'SERVIÇOS DO PEDIDO' if '\\n' in str(order['service']) else 'DESCRIÇÃO DO SERVIÇO',14,98)
        service_lines,multiple_services=service_lines_for_os(order['service'],68*MM,8.2)
        for i,part in enumerate(service_lines[:8 if multiple_services else 5]):
            text(c,part,14,104+i*(3.2 if multiple_services else 4.35),7.55 if multiple_services else 8.0)
        rect(c,14,127,68,.3,FRAME)
        label(c,'QTD.',14,132);text(c,order['quantity'],14,138,7.3,INK,True)
        label(c,'PRIORIDADE',34,132);text(c,order['priority'][:13],34,138,7,INK,True)
        label(c,'SITUAÇÃO',61,132);text(c,order['status'][:12],61,138,6.2,INK,True)
        rect(c,14,140,68,.3,FRAME)
        notes=pdf_wrap(order['notes'] or 'Sem observações adicionais.',68*MM,7.8)
        label(c,'OBSERVAÇÕES' if len(notes)<=2 else 'OBSERVAÇÕES (RESUMO)',14,145)
        for i,part in enumerate(notes[:2]):text(c,part,14,151+i*4,7.6)
        label(c,'IMAGEM DO PEDIDO',93,98)
        rect(c,93,101,41,46,'#f8f9fc',FRAME)
        factor=min(41*MM/photo['width'],46*MM/photo['height'])
        w,h=photo['width']*factor,photo['height']*factor
        x=93*MM+(41*MM-w)/2
        y=PH-101*MM-(46*MM-h)/2-h
        c.append(f'q {w:.2f} 0 0 {h:.2f} {x:.2f} {y:.2f} cm /Foto{photo_offset} Do Q')
        for i,part in enumerate(pdf_wrap(photo['name'],41*MM,6.3)[:2]):
            text(c,part,93,152+i*3.2,6.2,MUT)
    else:
        service_lines,multiple_services=service_lines_for_os(order['service'],119*MM,9.3)
        if multiple_services:
            panel(c,91,68)
            label(c,'SERVIÇOS DO PEDIDO',14,98)
            for i,part in enumerate(service_lines[:8]):text(c,part,14,104+i*3.2,8.1)
            rect(c,14,131,120,.3,FRAME)
            label(c,'QTD.',14,136);text(c,order['quantity'],27,136,7.6,INK,True)
            label(c,'PRIORIDADE',45,136);text(c,order['priority'][:16],69,136,7.6,INK,True)
            label(c,'SITUAÇÃO',101,136);text(c,order['status'][:15],101,140,6.8,INK,True)
            rect(c,14,142,120,.3,FRAME)
            label(c,'OBSERVAÇÕES / ACABAMENTO',14,147)
            notes=pdf_wrap(order['notes'] or 'Sem observações adicionais.',119*MM,8)
            for i,part in enumerate(notes[:2]):text(c,part,14,152+i*4.0,7.7)
        else:
            panel(c,91,33)
            label(c,'DESCRIÇÃO DO SERVIÇO',14,98)
            for i,part in enumerate(service_lines[:3]):text(c,part,14,104+i*5,9.3)
            rect(c,14,115.5,120,.3,FRAME)
            label(c,'QTD.',14,119.5);text(c,order['quantity'],27,119.5,7.8,INK,True)
            label(c,'PRIORIDADE',45,119.5);text(c,order['priority'][:16],69,119.5,7.8,INK,True)
            label(c,'SITUAÇÃO',101,118);text(c,order['status'][:15],101,122,6.8,INK,True)
            panel(c,127,32)
            label(c,'OBSERVAÇÕES / ACABAMENTO',14,134)
            notes=pdf_wrap(order['notes'] or 'Sem observações adicionais.',119*MM,8.5)
            for i,part in enumerate(notes[:4]):text(c,part,14,141+i*4.3,8.4)
    if len(name_lines)>1 or len(service_lines)>(8 if multiple_services else (5 if photo else 3)) or len(notes)>(2 if (photo or multiple_services) else 4):
        text(c,'Informações extensas: consulte o pedido completo no sistema.',14,160.4,5.2,MUT)
    panel(c,162,18)
    label(c,'VALOR TOTAL',14,168);text(c,brl(order['total']),14,176,9.2,INK,True)
    label(c,'RECEBIDO',57,168);text(c,brl(order['received']),57,176,9,INK,True)
    label(c,'SALDO A RECEBER',97,168);text(c,brl(order['balance']),97,176,8.8,INK,True)
    text(c,'PAGAMENTO: '+order['payment'],14,179,6.1,MUT)
    rect(c,12,190,54,.25,'#b9c0d6');rect(c,83,190,54,.25,'#b9c0d6')
    label(c,'RESPONSÁVEL / PRODUÇÃO',13,195);label(c,'ASSINATURA DO CLIENTE',85,195)
    text(c,'Gráfica Carvalho  •  Folha '+footer_label,11,205,6.1,MUT)
    pages=[c]
    return pages,png_logo

def make_pdf(order, mode='a5', color='color'):
    """Uma OS em A5 real ou uma OS ampliada para a folha A4 retrato."""
    if color not in ('color','bw'):raise AppError(422,'Cor da OS inválida.')
    photos=load_os_photos(order['id'],color)[:1]
    pages,logo=make_os_pages(order, 'A4 (210 x 297 mm)' if mode=='a4' else 'A5 (148 x 210 mm)',
                             color=color,photos=photos)
    if mode=='a5':
        return PDF().build(pages,logo,extra_images=photos)
    if mode=='a4':
        # O A4 tem a mesma proporção ISO da A5, escalado 141,4%.
        scale=A4_H/PH
        shift=(A4_W-PW*scale)/2
        bigger=[[f'q {scale:.8f} 0 0 {scale:.8f} {shift:.4f} 0 cm',*c,'Q'] for c in pages]
        return PDF().build(bigger,logo,page_w=A4_W,page_h=A4_H,extra_images=photos)
    raise AppError(422,'Formato de impressão inválido.')

def make_pdf_double(first,second,color='color'):
    """Duas OS distintas, 148 x 210 mm cada, lado a lado em A4 horizontal.

    O A4 mede 297 x 210 mm, deixando 0,5 mm de espaço central total
    entre os dois formatos A5 de 148 x 210 mm.
    Observações extensas continuam nas páginas horizontais seguintes.
    """
    if first['id']==second['id']:
        raise AppError(422,'Escolha duas ordens de serviço diferentes.')
    if color not in ('color','bw'):raise AppError(422,'Cor da OS inválida.')
    images_left=load_os_photos(first['id'],color)[:1]
    images_right=load_os_photos(second['id'],color)[:1]
    left,logo=make_os_pages(first,color=color,photos=images_left)
    right,_=make_os_pages(second,color=color,photos=images_right,photo_offset=len(images_left))
    # Cada uma fica centralizada em sua metade de A4 (148,5 mm).
    margin=(A4_LAND_W/2-PW)/2
    pages=[]
    for i in range(max(len(left),len(right))):
        cmds=[]
        if i<len(left):
            cmds.extend(['q',f'1 0 0 1 {margin:.4f} 0 cm',*left[i],'Q'])
        if i<len(right):
            cmds.extend(['q',f'1 0 0 1 {A4_LAND_W/2+margin:.4f} 0 cm',*right[i],'Q'])
        # Linha de corte suave no centro da folha; não invade o conteúdo.
        x=A4_LAND_W/2
        cmds.append(f'q 0.78 0.80 0.86 RG 0.5 w [3 4] 0 d {x:.3f} 3 m {x:.3f} {A4_LAND_H-3:.3f} l S Q')
        pages.append(cmds)
    return PDF().build(pages,logo,page_w=A4_LAND_W,page_h=A4_LAND_H,extra_images=images_left+images_right)

def brl(v):return 'R$ '+f'{v:,.2f}'.replace(',','@').replace('.',',').replace('@','.')
def format_date(d):
    try:return date.fromisoformat(d).strftime('%d/%m/%Y')
    except(ValueError,TypeError):return str(d)

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    server_version='GraficaCarvalhoOffline/1.1'
    def log_message(self,fmt,*args):
        # Não registrar nomes de clientes ou payloads sensíveis.
        if os.environ.get('GRAFICA_LOG')=='1':super().log_message(fmt,*args)
    def reply(self,status,data=b'',ctype='application/json; charset=utf-8',extra=None):
        if isinstance(data,(dict,list)):
            data=json.dumps(data,ensure_ascii=False,allow_nan=False).encode('utf-8')
        if isinstance(data,str):data=data.encode('utf-8')
        self.send_response(status)
        # Política CORS restrita à origem HTTPS explicitamente configurada.
        origin=self.headers.get('Origin','').rstrip('/')
        if origin in self.allowed_origins():
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
            self.send_header('Access-Control-Allow-Methods','GET, POST, PUT, DELETE, OPTIONS')
            self.send_header('Access-Control-Allow-Headers','Authorization, Content-Type, X-CSRF-Token')
            self.send_header('Access-Control-Expose-Headers','Content-Disposition')
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(len(data)))
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','no-referrer')
        self.send_header('Permissions-Policy','camera=(), microphone=(), geolocation=()')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if extra:
            for k,v in extra.items():self.send_header(k,v)
        self.end_headers()
        if data:self.wfile.write(data)
    def json_body(self,limit=1024*1024):
        if not self.headers.get('Content-Type','').lower().startswith('application/json'):
            raise AppError(415,'Esperado conteúdo JSON.')
        raw=self.read_body(limit)
        try:return json.loads(raw)
        except(ValueError,UnicodeError):raise AppError(400,'JSON inválido.')
    def read_body(self,limit):
        try:n=int(self.headers.get('Content-Length','0'))
        except ValueError:raise AppError(400,'Content-Length inválido')
        if n<0 or n>limit:raise AppError(413,'Envio maior que o limite permitido.')
        return self.rfile.read(n)
    def files(self,limit):
        ct=self.headers.get('Content-Type','')
        if not ct.lower().startswith('multipart/form-data') or 'boundary=' not in ct:
            raise AppError(415,'Esperado upload de arquivo.')
        raw=self.read_body(limit)
        head=('Content-Type: '+ct+'\r\nMIME-Version: 1.0\r\n\r\n').encode('utf-8')
        try:msg=BytesParser(policy=policy.default).parsebytes(head+raw)
        except Exception:raise AppError(400,'Upload incompleto.')
        if not msg.is_multipart():raise AppError(400,'Upload inválido.')
        out=[]
        for part in msg.iter_parts():
            if part.get_content_disposition()!='form-data':continue
            name=part.get_param('name',header='content-disposition')
            filename=part.get_filename()
            blob=part.get_payload(decode=True) or b''
            out.append((name,filename,part.get_content_type(),blob))
        return out
    @staticmethod
    def wix_origin():
        origin=os.environ.get('GRAFICA_WIX_ORIGIN','').strip().rstrip('/')
        if origin and not origin.startswith('https://'):
            return ''
        return origin
    @staticmethod
    def allowed_origins():
        # Mantém o endereço Wix antigo e o novo endereço gratuito do painel.
        # Não se usa '*' para liberar origens arbitrárias.
        return {origin for origin in (
            Handler.wix_origin(),
            'https://grafica-carvalho.onrender.com'
        ) if origin}
    def do_OPTIONS(self):
        origin=self.headers.get('Origin','').rstrip('/')
        if not self.path.startswith('/api/') or not origin or origin not in self.allowed_origins():
            return self.reply(403,{'detail':'Origem não autorizada.'})
        return self.reply(204,b'')
    def do_GET(self):self.guard('GET')
    def do_POST(self):self.guard('POST')
    def do_PUT(self):self.guard('PUT')
    def do_DELETE(self):self.guard('DELETE')
    def guard(self,method):
        try:self.route(method)
        except security.AuthError as exc:self.reply(exc.status,{'detail':exc.message})
        except print_queue.PrintError as exc:self.reply(exc.status,{'detail':exc.message})
        except AppError as exc:self.reply(exc.status,{'detail':exc.detail})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception as exc:
            print('[Erro interno]',repr(exc),flush=True)
            self.reply(500,{'detail':'Falha interna no sistema. Verifique a janela do Python.'})
    def route(self,method):
        u=urlparse(self.path);path=unquote(u.path);q=parse_qs(u.query)
        param=lambda name,default='':q.get(name,[default])[0]
        # O provedor HTTPS termina TLS no proxy e as rotas com dados exigem sessão.
        if method=='GET' and path=='/healthz':
            with connect() as db:db.execute('SELECT 1')
            return self.reply(200,{'ok':True})
        if method=='GET' and path=='/static/logo_carvalho.png':
            return self.reply(200,LOGO.read_bytes(),'image/png')
        if method in ('POST','PUT','DELETE'):
            origin=self.headers.get('Origin')
            if origin:
                from urllib.parse import urlsplit
                actual=urlsplit(origin)
                host=self.headers.get('Host','')
                if actual.scheme not in ('http','https') or (actual.netloc.lower()!=host.lower() and origin.rstrip('/') not in self.allowed_origins()):
                    raise security.AuthError(403,'Origem da solicitação não autorizada.')
        if path=='/login':
            if method!='GET':raise AppError(405,'Método não permitido.')
            user=security.session(self.headers)
            if user:return self.reply(302,b'','text/plain',{'Location':'/portal' if user['role']=='client' else '/'})
            return self.reply(200,(STATIC/'login.html').read_bytes(),'text/html; charset=utf-8')
        if path in ('/api/auth/login','/api/auth/token-login') and method=='POST':
            body=self.json_body(8192)
            if not isinstance(body,dict):raise AppError(422,'Informe usuário e senha.')
            token,data=security.login(body.get('email'),body.get('password'))
            secure=bool(os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID')) or os.environ.get('GRAFICA_SECURE_COOKIES')=='1'
            result={'user':{k:v for k,v in data.items() if k!='csrf'},'redirect':'/portal' if data['role']=='client' else '/'}
            if path=='/api/auth/token-login':
                # Token opaco usado apenas pelo frontend do Wix; nunca enviar cookie cross-site.
                result['access_token']=token
                return self.reply(200,result)
            return self.reply(200,result,extra={'Set-Cookie':security.secure_cookie(token,secure)})
        # O agente local consulta o servidor por HTTPS, sem abrir portas.
        if path.startswith('/api/print-agent/'):
            print_queue.agent_auth(self.headers)
            if method=='POST' and path=='/api/print-agent/heartbeat':
                return self.reply(200,print_queue.heartbeat(self.json_body(8192)))
            if method=='GET' and path=='/api/print-agent/next':
                return self.reply(200,print_queue.claim_next())
            if method=='POST' and path=='/api/print-agent/report':
                return self.reply(200,print_queue.report(self.json_body(8192)))
            raise AppError(404,'Recurso do agente não encontrado.')
        user=security.session(self.headers)
        if path=='/api/auth/me' and method=='GET':
            if not user:return self.reply(200,{'authenticated':False})
            return self.reply(200,{'authenticated':True,'user':security.public_user(user),'csrf':user['csrf_token']})
        if not user:
            if method=='GET' and path in ('/','/portal','/usuarios'):return self.reply(302,b'','text/plain',{'Location':'/login'})
            raise security.AuthError(401,'Faça login para continuar.')
        if method in ('POST','PUT','DELETE'):security.csrf(user,self.headers)
        if path=='/api/auth/logout' and method=='POST':
            security.logout(self.headers)
            secure=bool(os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID')) or os.environ.get('GRAFICA_SECURE_COOKIES')=='1'
            return self.reply(200,{'ok':True},extra={'Set-Cookie':security.secure_cookie('',secure,remove=True)})
        if path=='/api/auth/password' and method=='POST':
            body=self.json_body(8192)
            security.change_password(user['id'],body.get('current'),body.get('new'))
            secure=bool(os.environ.get('RENDER') or os.environ.get('RENDER_SERVICE_ID')) or os.environ.get('GRAFICA_SECURE_COOKIES')=='1'
            return self.reply(200,{'ok':True,'relogin':True},extra={'Set-Cookie':security.secure_cookie('',secure,remove=True)})
        if path=='/conta' and method=='GET':
            return self.reply(200,(STATIC/'conta.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/portal' and method=='GET':
            security.require_role(user,('client',))
            return self.reply(200,(STATIC/'portal.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/api/portal/orders' and method=='GET':
            security.require_role(user,('client',))
            with connect() as db:
                rows=db.execute('SELECT id,code,service,quantity,entry_date,due_date,status,priority FROM orders WHERE client_id=? ORDER BY created_at DESC',(user['client_id'],)).fetchall()
            return self.reply(200,{'items':[dict(row) for row in rows]})
        mo=re.fullmatch(r'/api/portal/orders/([a-f0-9]{32})',path)
        if mo and method=='GET':
            security.require_role(user,('client',))
            with connect() as db:
                row=db.execute('SELECT code,service,quantity,entry_date,due_date,status,priority FROM orders WHERE id=? AND client_id=?',(mo.group(1),user['client_id'])).fetchone()
            if not row:raise AppError(404,'Pedido não encontrado.')
            return self.reply(200,dict(row))
        if path=='/usuarios' and method=='GET':
            security.require_role(user,('admin',))
            return self.reply(200,(STATIC/'usuarios.html').read_bytes(),'text/html; charset=utf-8')
        if path=='/api/users':
            security.require_role(user,('admin',))
            if method=='GET':return self.reply(200,{'items':security.list_users()})
            if method=='POST':return self.reply(201,security.create_user(self.json_body(16384)))
        match_user=re.fullmatch(r'/api/users/([a-f0-9]{32})',path)
        if match_user:
            security.require_role(user,('admin',))
            if method=='PUT':return self.reply(200,security.update_user(match_user.group(1),self.json_body(16384),user['id']))
        # Cliente nunca acessa endpoints internos da equipe, banco ou arquivos.
        security.require_role(user,('admin','staff'))
        if method=='GET' and path=='/':return self.reply(200,(STATIC/'index.html').read_bytes(),'text/html; charset=utf-8')
        # Backup/restauração são operações exclusivamente administrativas.
        if path in ('/api/backup','/api/restore'):
            security.require_role(user,('admin',))
        if path=='/api/orders' and method=='DELETE':security.require_role(user,('admin',))
        if method=='DELETE' and re.fullmatch(r'/api/(orders|clients|attachments)/[a-f0-9]{32}',path):
            security.require_role(user,('admin',))

        if method=='GET' and path=='/static/logo_carvalho.png':return self.reply(200,LOGO.read_bytes(),'image/png')
        if path=='/api/print/status' and method=='GET':
            return self.reply(200, print_queue.status())
        if path=='/api/print/agent/key' and method=='POST':
            security.require_role(user,('admin',))
            return self.reply(200, print_queue.rotate_key())
        if path=='/api/print/jobs' and method=='POST':
            return self.reply(201, print_queue.enqueue(
                self.json_body(8192), user['id'], fetch_order, make_pdf, make_pdf_double))
        if path=='/api/clients' and method=='GET':return self.reply(200,list_clients(param('search')))
        if path=='/api/clients' and method=='POST':return self.reply(201,create_client(self.json_body()))
        if path=='/api/next-code' and method=='GET':
            with connect() as db:
                estimated=highwater(db)+1
                while db.execute('SELECT 1 FROM orders WHERE LOWER(code)=LOWER(?)',(f'GC-{estimated:06d}',)).fetchone():estimated+=1
            return self.reply(200,{'next_code':f'GC-{estimated:06d}','note':'Prévia; o código definitivo é reservado ao salvar.'})
        mclient=re.fullmatch(r'/api/clients/([a-fA-F0-9]{32})',path)
        if mclient:
            cid=mclient.group(1)
            if method=='GET':
                with connect() as db:return self.reply(200,client_detail(db,cid))
            if method=='PUT':return self.reply(200,update_client(cid,self.json_body()))
            if method=='DELETE':delete_client(cid);return self.reply(204,b'')
        if path=='/api/orders' and method=='GET':return self.reply(200,orders(param('view','hoje'),param('day'),param('search')))
        if path=='/api/summary' and method=='GET':return self.reply(200,summary(param('day')))
        if path=='/api/orders' and method=='POST':return self.reply(201,create_order(self.json_body()))
        if method=='GET' and path=='/api/print/os':
            mode=param('mode','a4')
            id1=param('id1')
            id2=param('id2')
            color=param('color','color')
            if color not in ('color','bw'):
                raise AppError(422,'Selecione colorido ou preto e branco.')
            if not re.fullmatch(r'[a-fA-F0-9]{32}',id1):
                raise AppError(422,'Selecione a primeira ordem de serviço.')
            if mode not in ('a4','a5','dupla'):
                raise AppError(422,'Escolha uma modalidade de impressão válida.')
            if mode=='dupla':
                if not re.fullmatch(r'[a-fA-F0-9]{32}',id2):
                    raise AppError(422,'Selecione a segunda ordem de serviço.')
                if id1==id2: raise AppError(422,'Escolha duas ordens diferentes.')
            with connect() as db:
                first=fetch_order(db,id1)
                second=fetch_order(db,id2) if mode=='dupla' else None
            if mode=='dupla':
                pdf=make_pdf_double(first,second,color=color)
                filename='OS_DUAS_A5_EM_A4_HORIZONTAL_'+('PB' if color=='bw' else 'COLORIDA')+'.pdf'
            else:
                pdf=make_pdf(first,mode,color=color)
                code=re.sub('[^A-Za-z0-9_-]','_',first['code'] or id1[:10])[:45]
                filename=f'OS_{code}_{mode.upper()}_{"PB" if color=="bw" else "COLORIDA"}.pdf'
            disp='inline' if param('visualizar')=='1' else 'attachment'
            return self.reply(200,pdf,'application/pdf',{
                'Content-Disposition':f'{disp}; filename="{filename}"'})

        if path=='/api/backup' and method=='GET':
            return self.reply(200,backup(),'application/json',{'Content-Disposition':f'attachment; filename="backup_grafica_carvalho_{today()}.json"'})
        if path=='/api/restore' and method=='POST':
            parts=self.files(170*1024*1024)
            file=next((p[3] for p in parts if p[0]=='file'),None)
            if file is None:raise AppError(422,'Selecione o arquivo de backup JSON.')
            return self.reply(200,restore(file))
        m=re.fullmatch(r'/api/orders/([a-fA-F0-9]{32})(?:/(attachments|pdf))?',path)
        if m:
            oid,sub=m.group(1),m.group(2)
            if sub is None:
                if method=='GET':
                    with connect() as db:data=fetch_order(db,oid)
                    return self.reply(200,data)
                if method=='PUT':return self.reply(200,update_order(oid,self.json_body()))
                if method=='DELETE':
                    with connect() as db:
                        fetch_order(db,oid)
                        db.execute('DELETE FROM orders WHERE id=?',(oid,))
                    return self.reply(204,b'')
            elif sub=='attachments':
                if method=='GET':
                    with connect() as db:
                        fetch_order(db,oid)
                        arr=[dict(x) for x in db.execute('SELECT id,filename,content_type,size,created_at FROM attachments WHERE order_id=? ORDER BY created_at,id',(oid,)).fetchall()]
                    return self.reply(200,arr)
                if method=='POST':
                    files=[(filename or 'arquivo',mime,blob) for field,filename,mime,blob in self.files(MAX_UPLOAD_BODY) if field=='files']
                    return self.reply(201,store_attachments(oid,files))
            elif sub=='pdf' and method=='GET':
                with connect() as db:data=fetch_order(db,oid)
                color=param('color','color')
                if color not in ('color','bw'):raise AppError(422,'Cor da OS inválida.')
                b=make_pdf(data,color=color)
                code=re.sub('[^A-Za-z0-9_-]','_',data['code'] or oid[:10])[:48]
                return self.reply(200,b,'application/pdf',{'Content-Disposition':f'attachment; filename="OS_{code}_A5_{'PB' if color=='bw' else 'COLORIDA'}.pdf"'})
        m=re.fullmatch(r'/api/attachments/([a-fA-F0-9]{32})(?:/(preview|download))?',path)
        if m:
            aid,sub=m.groups()
            if method=='DELETE' and not sub:
                with connect() as db:
                    if not db.execute('SELECT id FROM attachments WHERE id=?',(aid,)).fetchone():raise AppError(404,'Anexo não encontrado')
                    db.execute('DELETE FROM attachments WHERE id=?',(aid,))
                return self.reply(204,b'')
            if method=='GET' and sub:
                item=get_attachment(aid)
                if sub=='preview':
                    if item['content_type'] not in ('image/png','image/jpeg','image/gif','image/webp','image/bmp'):
                        raise AppError(415,'Pré-visualização indisponível para esse formato.')
                    return self.reply(200,item['data'],item['content_type'])
                name=quote(item['filename'],safe='')
                return self.reply(200,item['data'],'application/octet-stream',{'Content-Disposition':f"attachment; filename*=UTF-8''{name}"})
        raise AppError(404,'Página ou recurso não encontrado.')

def main():
    # Render gratuito não tem disco persistente. PostgreSQL Neon é obrigatório
    # em todas as leituras e gravações (inclusive sessões e anexos).
    database.connection_string()
    init_db()
    security.setup()
    try:port=int(os.environ.get('PORT',os.environ.get('GRAFICA_PORT','8000')))
    except ValueError:port=8000
    host=os.environ.get('GRAFICA_BIND_HOST','0.0.0.0')
    print(f'Gráfica Carvalho: {host}:{port}; Banco: PostgreSQL externo (Neon)',flush=True)
    server=ThreadingHTTPServer((host,port),Handler)
    try:server.serve_forever()
    except KeyboardInterrupt:print('Sistema encerrado.')
    finally:server.server_close()

if __name__=='__main__':main()
