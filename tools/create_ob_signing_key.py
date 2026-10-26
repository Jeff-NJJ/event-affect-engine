"""Create a new local archive-signing key without printing it. Never overwrite."""
import os
from pathlib import Path
import secrets
import sys

def main():
    if len(sys.argv)!=2:raise SystemExit('Usage: create_ob_signing_key.py NEW_PRIVATE_PATH')
    p=Path(sys.argv[1])
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as f:f.write(secrets.token_hex(32).encode()+b'\n');f.flush();os.fsync(f.fileno())
    print('New signing key created; value not displayed')

if __name__=='__main__':main()
