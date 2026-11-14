"""Independent local application for the Workspace read/write connector example.

python examples/notes_app.py --data-dir .local/example-notes --port 8890
"""
import argparse
import json
import sqlite3
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
import uvicorn


class Item(BaseModel):
    title:str=Field(min_length=1,max_length=500)
    body:str=Field(max_length=20000)
    action_id:str=Field(min_length=16,max_length=128)


def create_app(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True);path=root/'notes.sqlite3'
    with sqlite3.connect(path) as db:db.execute('CREATE TABLE IF NOT EXISTS items(action_id TEXT PRIMARY KEY,title TEXT NOT NULL,body TEXT NOT NULL)')
    app=FastAPI(title='Local connector example')
    @app.middleware('http')
    async def boundary(request:Request,call_next):
        if request.headers.get('origin') or request.headers.get('host','').split(':')[0] not in {'127.0.0.1','localhost'}:raise HTTPException(403,'Native local connector only')
        return await call_next(request)
    @app.get('/items')
    def items():
        with sqlite3.connect(path) as db:
            db.row_factory=sqlite3.Row
            return {'items':[dict(row) for row in db.execute('SELECT * FROM items ORDER BY rowid')]}
    @app.post('/items')
    def add(item:Item):
        with sqlite3.connect(path) as db:
            old=db.execute('SELECT title,body FROM items WHERE action_id=?',(item.action_id,)).fetchone()
            if old and old!=(item.title,item.body):raise HTTPException(409,'Action ID conflict')
            db.execute('INSERT OR IGNORE INTO items VALUES(?,?,?)',(item.action_id,item.title,item.body))
        return {**item.model_dump(),'saved':True}
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',default='.local/example-notes');parser.add_argument('--port',type=int,default=8890)
    args=parser.parse_args();uvicorn.run(create_app(args.data_dir),host='127.0.0.1',port=args.port)
