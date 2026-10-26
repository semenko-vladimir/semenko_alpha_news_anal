"""Private Linux socket -> fixed broker CLI. No credential reads or public port."""
import argparse
import json
import os
from pathlib import Path
import socketserver
import signal
import subprocess
import sys
import threading

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from backend.model_clients import ALLOWED_MODELS,MAX_STDOUT_BYTES


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket',type=Path,required=True)
    parser.add_argument('--broker-script',type=Path,required=True)
    parser.add_argument('--assignment',default='FINAL-PRODUCT-001')
    args=parser.parse_args();target=args.socket.resolve()
    if not args.broker_script.is_absolute() or not args.broker_script.is_file():parser.error('Existing fixed broker code is required')
    if target.exists():parser.error('Socket already exists; owner must be checked before restart')
    target.parent.mkdir(parents=True,exist_ok=True);target.parent.chmod(0o770)
    lock=threading.Lock()
    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(115)
            try:
                raw=self.rfile.readline(60001)
                if not raw.endswith(b'\n') or len(raw)>60000:raise ValueError()
                data=json.loads(raw)
                if data=={'action':'status'}:action='status'
                elif (isinstance(data,dict) and set(data)=={'task_id','model','prompt'} and
                      isinstance(data['task_id'],str) and data['task_id'].startswith(args.assignment+':') and
                      len(data['task_id'])<=100 and data['model'] in ALLOWED_MODELS and
                      isinstance(data['prompt'],str) and 0<len(data['prompt'])<=12000):action='run'
                else:raise ValueError()
                with lock:
                    process=subprocess.run([sys.executable,str(args.broker_script),action],
                        input=json.dumps(data,ensure_ascii=False) if action=='run' else None,
                        capture_output=True,text=True,encoding='utf-8',timeout=105)
                body=process.stdout.strip()
                if not body or len(body.encode('utf-8'))>MAX_STDOUT_BYTES:raise ValueError()
                json.loads(body)
                self.wfile.write(body.encode('utf-8')+b'\n')
            except Exception:
                self.wfile.write(b'{"status":"error","error":"private_bridge_unavailable"}\n')
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads=False
    stopped=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:stopped.set())
    signal.signal(signal.SIGINT,lambda *_:stopped.set())
    server=Server(str(target),Handler);server.timeout=.5;target.chmod(0o660)
    try:
        while not stopped.is_set():server.handle_request()
    finally:
        server.server_close()
        if target.is_socket():target.unlink()


if __name__=='__main__':main()
