"""Fila segura de impressao no Neon; agente Windows consulta via HTTPS, sem portas expostas."""
import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid
from database import connect

class PrintError(Exception):
    def __init__(self,status,message):
        self.status=status
        self.message=message
        super().__init__(message)

def setup():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS gc_print_agent (
            id INTEGER PRIMARY KEY CHECK(id=1),
            token_hash TEXT NOT NULL,
            last_seen BIGINT NOT NULL DEFAULT 0,
            printer1_name TEXT NOT NULL DEFAULT '',
            printer2_name TEXT NOT NULL DEFAULT '')""")
        db.execute("""CREATE TABLE IF NOT EXISTS gc_print_jobs (
            id TEXT PRIMARY KEY,
            created_by TEXT NOT NULL,
            printer_slot TEXT NOT NULL CHECK(printer_slot IN ('printer1','printer2')),
            mode TEXT NOT NULL CHECK(mode IN ('a4','a5','dupla')),
            copies INTEGER NOT NULL CHECK(copies BETWEEN 1 AND 5),
            status TEXT NOT NULL CHECK(status IN ('pending','processing','sent','failed')),
            created_at BIGINT NOT NULL,
            updated_at BIGINT NOT NULL,
            pdf BYTEA NOT NULL,
            report TEXT NOT NULL DEFAULT '')""")
        db.execute('CREATE INDEX IF NOT EXISTS ix_gc_print_jobs_status ON gc_print_jobs(status,created_at)')

def agent_auth(headers):
    token=headers.get('X-Print-Agent-Token','')
    if not 40<=len(token)<=200:
        raise PrintError(401,'Agente nao autorizado.')
    digest=hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        row=db.execute('SELECT token_hash FROM gc_print_agent WHERE id=1').fetchone()
    if not row or not hmac.compare_digest(row['token_hash'],digest):
        raise PrintError(401,'Agente nao autorizado.')

def status():
    now=int(time.time())
    with connect() as db:
        agent=db.execute('SELECT last_seen,printer1_name,printer2_name FROM gc_print_agent WHERE id=1').fetchone()
        recent=db.execute("""SELECT id,printer_slot,mode,copies,status,created_at,updated_at,report
            FROM gc_print_jobs ORDER BY created_at DESC,id DESC LIMIT 15""").fetchall()
    last=agent['last_seen'] if agent else 0
    return {'configured':bool(agent),'online':bool(last and now-last<=90),
        'last_seen':last or None,
        'printers':{'printer1':agent['printer1_name'] if agent else '',
                    'printer2':agent['printer2_name'] if agent else ''},
        'jobs':[dict(x) for x in recent]}

def rotate_key():
    token=secrets.token_urlsafe(48)
    digest=hashlib.sha256(token.encode()).hexdigest()
    with connect() as db:
        db.execute("""INSERT INTO gc_print_agent(id,token_hash,last_seen,printer1_name,printer2_name)
            VALUES (1,?,0,'','') ON CONFLICT (id) DO UPDATE
            SET token_hash=EXCLUDED.token_hash,last_seen=0,printer1_name='',printer2_name=''""",(digest,))
    return {'token':token,'notice':'Chave exibida apenas agora. Gerar outra invalida o agente anterior.'}

def enqueue(payload,user_id,fetch_order,make_pdf,make_pdf_double):
    if not isinstance(payload,dict):raise PrintError(422,'Solicitacao invalida.')
    slot=payload.get('printer')
    mode=payload.get('mode')
    if slot not in ('printer1','printer2') or mode not in ('a4','a5','dupla'):
        raise PrintError(422,'Escolha impressora e formato.')
    copies=payload.get('copies',1)
    if type(copies) is not int or not 1<=copies<=5:
        raise PrintError(422,'Escolha de 1 a 5 copias.')
    id1,id2=str(payload.get('id1') or ''),str(payload.get('id2') or '')
    if not re.fullmatch(r'[a-fA-F0-9]{32}',id1):
        raise PrintError(422,'Primeira OS invalida.')
    if mode=='dupla' and (not re.fullmatch(r'[a-fA-F0-9]{32}',id2) or id1==id2):
        raise PrintError(422,'Escolha duas ordens diferentes.')
    timestamp=int(time.time())
    with connect() as db:
        agent=db.execute('SELECT last_seen,printer1_name,printer2_name FROM gc_print_agent WHERE id=1').fetchone()
        if not agent or timestamp-agent['last_seen']>90:
            raise PrintError(409,'Computador de impressao desconectado. Abra o agente Windows.')
        if not agent[slot+'_name']:
            raise PrintError(409,'Impressora nao configurada no agente Windows.')
        first=fetch_order(db,id1)
        second=fetch_order(db,id2) if mode=='dupla' else None
        pdf=make_pdf_double(first,second) if second is not None else make_pdf(first,mode)
        if not pdf.startswith(b'%PDF-') or len(pdf)>5*1024*1024:
            raise PrintError(413,'PDF invalido ou muito grande.')
        job_id=uuid.uuid4().hex
        db.execute("""INSERT INTO gc_print_jobs
            (id,created_by,printer_slot,mode,copies,status,created_at,updated_at,pdf,report)
            VALUES (?,?,?,?,?,'pending',?,?,?,'')""",
            (job_id,user_id,slot,mode,copies,timestamp,timestamp,pdf))
        db.execute("DELETE FROM gc_print_jobs WHERE created_at < ? AND status IN ('sent','failed')",
                   (timestamp-14*86400,))
    return {'job_id':job_id,'status':'pending','message':'OS encaminhada para impressao.'}

def heartbeat(payload):
    if not isinstance(payload,dict):raise PrintError(422,'Dados invalidos.')
    names={}
    for slot in ('printer1','printer2'):
        name=payload.get(slot)
        if not isinstance(name,str) or not 1<=len(name)<=180 or not name.strip():
            raise PrintError(422,'Configure o nome das duas impressoras Windows.')
        names[slot]=name.strip()
    if names['printer1']==names['printer2']:
        raise PrintError(422,'Escolha duas filas de impressao diferentes.')
    with connect() as db:
        db.execute('UPDATE gc_print_agent SET last_seen=?,printer1_name=?,printer2_name=? WHERE id=1',
                   (int(time.time()),names['printer1'],names['printer2']))
    return {'ok':True}

def claim_next():
    timestamp=int(time.time())
    with connect() as db:
        # NUNCA reimprimir automaticamente se o PC cair depois de enviar ao spooler.
        job=db.execute("""SELECT id,printer_slot,mode,copies,pdf FROM gc_print_jobs
            WHERE status='pending' ORDER BY created_at,id LIMIT 1 FOR UPDATE SKIP LOCKED""").fetchone()
        if not job:return {'job':None}
        db.execute("UPDATE gc_print_jobs SET status='processing',updated_at=? WHERE id=?",
                   (timestamp,job['id']))
        blob=bytes(job['pdf'])
        return {'job':{'id':job['id'],'printer':job['printer_slot'],'mode':job['mode'],
            'copies':job['copies'],'pdf_base64':base64.b64encode(blob).decode('ascii'),
            'sha256':hashlib.sha256(blob).hexdigest()}}

def report(payload):
    if not isinstance(payload,dict):raise PrintError(422,'Dados invalidos.')
    job_id=payload.get('job_id','')
    if not isinstance(job_id,str) or not re.fullmatch(r'[a-f0-9]{32}',job_id):
        raise PrintError(422,'Identificador invalido.')
    success=payload.get('success')
    if type(success) is not bool:raise PrintError(422,'Resultado invalido.')
    msg=str(payload.get('message',''))[:400]
    with connect() as db:
        row=db.execute('SELECT status FROM gc_print_jobs WHERE id=? FOR UPDATE',(job_id,)).fetchone()
        if not row:raise PrintError(404,'Trabalho nao encontrado.')
        if row['status'] in ('sent','failed'):return {'ok':True,'status':row['status']}
        if row['status']!='processing':raise PrintError(409,'Impressao nao estava em andamento.')
        status='sent' if success else 'failed'
        db.execute('UPDATE gc_print_jobs SET status=?,updated_at=?,report=? WHERE id=?',
                   (status,int(time.time()),msg,job_id))
    return {'ok':True,'status':status}
