"""Create private demo access files. Passwords and TLS private keys are never printed."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import subprocess


def configure(output, hostname, *, username='judge'):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Existing access configuration preserved; use a new directory')
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}',hostname):raise ValueError('Invalid certificate hostname')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}',username):raise ValueError('Invalid username')
    output.mkdir(parents=True,mode=0o700)
    password=secrets.token_urlsafe(24)
    # Only this child process receives the generated demo password, via stdin.
    encoded=subprocess.run(['openssl','passwd','-6','-stdin'],input=password+'\n',
        text=True,capture_output=True,check=True).stdout.strip()
    if not encoded.startswith('$6$'):raise ValueError('Password hashing failed')
    (output/'htpasswd').write_text(username+':'+encoded+'\n',encoding='utf-8')
    try:ipaddress.ip_address(hostname);kind='IP'
    except ValueError:kind='DNS'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-sha256','-days','14','-nodes',
        '-keyout',str(output/'key.pem'),'-out',str(output/'cert.pem'),'-subj','/CN='+hostname,
        '-addext','subjectAltName='+kind+':'+hostname],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
    (output/'operator-access.txt').write_text('Username: '+username+'\nPassword: '+password+
        '\nLocal self-signed TLS certificate; replace with a trusted certificate for public HTTPS.\n',encoding='utf-8')
    paths={'PRODUCT_TLS_CERT':output/'cert.pem','PRODUCT_TLS_KEY':output/'key.pem','PRODUCT_AUTH_FILE':output/'htpasswd'}
    (output/'access.env').write_text('\n'.join(k+'="'+v.as_posix()+'"' for k,v in paths.items())+'\n',encoding='utf-8')
    if os.name!='nt':
        for name in ['key.pem','cert.pem','htpasswd']:(output/name).chmod(0o640)
        for name in ['operator-access.txt','access.env']:(output/name).chmod(0o600)
    return {'status':'configured','operator_file':str(output/'operator-access.txt'),
            'environment_file':str(output/'access.env'),'tls':'self_signed_14_days','password_printed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--hostname',required=True)
    args=parser.parse_args()
    try:result=configure(args.output,args.hostname)
    except (OSError,ValueError,subprocess.SubprocessError) as exc:
        # Do not include subprocess input, credential contents, or private paths in errors.
        parser.exit(1,'Access configuration failed: '+type(exc).__name__+'\n')
    print(json.dumps(result))


if __name__=='__main__':main()
