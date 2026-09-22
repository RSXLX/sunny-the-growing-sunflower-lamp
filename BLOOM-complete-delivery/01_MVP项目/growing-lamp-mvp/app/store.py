"""SQLite persistence. Every connection enforces foreign keys and a lock timeout."""
from __future__ import annotations
import hashlib, hmac, json, os, secrets, sqlite3, time, uuid
from pathlib import Path
from contextlib import contextmanager
from .migrations import migrate

def uid(): return uuid.uuid4().hex

def stamp(): return int(time.time())

def hash_secret(value): return hashlib.sha256(value.encode()).hexdigest()

def password_hash(password,salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+':'+hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(salt),240000).hex()

def verify_password(password,encoded):
    try: return hmac.compare_digest(password_hash(password,encoded.split(':')[0]),encoded)
    except (ValueError,TypeError): return False

SCHEMA='''
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,name TEXT NOT NULL,email TEXT NOT NULL UNIQUE,password TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),csrf TEXT NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS memories(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,body TEXT NOT NULL,theme TEXT NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS designs(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES users(id),title TEXT NOT NULL,prompt TEXT NOT NULL,theme TEXT NOT NULL,seed INTEGER NOT NULL,source_id TEXT REFERENCES designs(id),memory_id TEXT REFERENCES memories(id) ON DELETE SET NULL,provider TEXT NOT NULL,status TEXT NOT NULL,progress INTEGER NOT NULL DEFAULT 0,task_id TEXT,error TEXT,asset TEXT,public INTEGER NOT NULL DEFAULT 0,license TEXT NOT NULL DEFAULT 'CC-BY-4.0',review_note TEXT,created INTEGER NOT NULL,next_run INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,idempotency_key TEXT,UNIQUE(owner,idempotency_key));
CREATE TABLE IF NOT EXISTS instances(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES users(id),design_id TEXT NOT NULL REFERENCES designs(id),name TEXT NOT NULL,tag_payload TEXT NOT NULL UNIQUE,tag_uid TEXT,stage TEXT NOT NULL DEFAULT 'planned',simulated INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS devices(id TEXT PRIMARY KEY,owner TEXT NOT NULL REFERENCES users(id),name TEXT NOT NULL,token TEXT NOT NULL UNIQUE,kind TEXT NOT NULL,reported TEXT NOT NULL DEFAULT '{}',seen INTEGER NOT NULL DEFAULT 0,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS commands(id TEXT PRIMARY KEY,device_id TEXT NOT NULL REFERENCES devices(id),kind TEXT NOT NULL,payload TEXT NOT NULL,state TEXT NOT NULL DEFAULT 'queued',error TEXT,created INTEGER NOT NULL,expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,device_id TEXT NOT NULL REFERENCES devices(id),kind TEXT NOT NULL,payload TEXT NOT NULL,simulated INTEGER NOT NULL,created INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS favorites(owner TEXT NOT NULL REFERENCES users(id),design_id TEXT NOT NULL REFERENCES designs(id),PRIMARY KEY(owner,design_id));
CREATE INDEX IF NOT EXISTS design_jobs ON designs(status,next_run);
CREATE INDEX IF NOT EXISTS memory_owner ON memories(owner,created);
CREATE INDEX IF NOT EXISTS command_queue ON commands(device_id,state,expires);
CREATE INDEX IF NOT EXISTS event_device ON events(device_id,created);
'''

class Store:
    def __init__(self,directory):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.path=self.directory/'bloom.sqlite3'
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL');db.executescript(SCHEMA)
        migrate(self.path)
    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=15); db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON');db.execute('PRAGMA busy_timeout=15000')
            with db:yield db
        finally:db.close()
    def all(self,sql,args=()):
        with self.connect() as db:return [dict(x) for x in db.execute(sql,args)]
    def one(self,sql,args=()):
        with self.connect() as db:
            row=db.execute(sql,args).fetchone();return dict(row) if row else None
    def execute(self,sql,args=()):
        with self.connect() as db:return db.execute(sql,args).rowcount
    def user(self,email,name,password):
        existing=self.one('SELECT id FROM users WHERE email=?',(email,))
        if existing:return existing['id']
        id=uid();self.execute('INSERT INTO users VALUES(?,?,?,?,?)',(id,name,email,password_hash(password),stamp()));return id
    def device(self,owner,name,kind='esp32'):
        id=uid(); token=secrets.token_urlsafe(32)
        state={'power':True,'brightness':45,'projection':0,'theme':'sunflower','instance_id':None,'temperature_c':None}
        self.execute('INSERT INTO devices VALUES(?,?,?,?,?,?,?,?)',(id,owner,name,hash_secret(token),kind,json.dumps(state),0,stamp()))
        return id,token
    def event(self,device,kind,payload,event_id=None):
        event_id=event_id or uid()
        self.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',(event_id,device['id'],kind,json.dumps(payload,ensure_ascii=False),int(device['kind']=='simulator'),stamp()))
