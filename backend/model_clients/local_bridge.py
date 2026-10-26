"""Private Unix socket transport; only the broker process reads its credential."""
import json
import socket
from pathlib import Path
from . import UnknownOutcome,MAX_STDOUT_BYTES


class LocalBrokerTransport:
    def __init__(self,path):
        self.path=str(Path(path))
        if not Path(self.path).is_absolute():raise ValueError('Broker socket path must be absolute')

    def request(self,value):
        payload=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()+b'\n'
        if len(payload)>60000:raise ValueError('Broker request too large')
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
                connection.settimeout(110);connection.connect(self.path);connection.sendall(payload)
                with connection.makefile('rb') as reader:reply=reader.readline(MAX_STDOUT_BYTES+1)
            if not reply or len(reply)>MAX_STDOUT_BYTES:raise UnknownOutcome('Broker response incomplete')
            return reply.decode('utf-8')
        except (OSError,UnicodeError) as exc:raise UnknownOutcome('Local broker result could not be confirmed') from exc

    def __call__(self,job):return self.request(job)

    def status(self):
        value=json.loads(self.request({'action':'status'}))
        if value.get('status')!='ok' or value.get('daily_attempt_limit')!=4:raise ValueError('Broker status unavailable')
        return value
