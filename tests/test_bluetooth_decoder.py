#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the actual release ARM64 decoder with known ATC data in a read-only container."""
import argparse
import json
from pathlib import Path
import subprocess

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--artifacts',required=True,type=Path);parser.add_argument('--image',required=True)
    args=parser.parse_args()
    frames=[{'id':'A4:C1:38:11:22:33','name':'ATC_112233','servicedatauuid':'181a','servicedata':'a4c13811223300fa32640bb801'},
            {'id':'00:11:22:33:44:55','name':'Unknown','servicedatauuid':'ffff','servicedata':'00'},{}]
    result=subprocess.run(['docker','run','--rm','-i','--platform','linux/arm64','--network','none',
        '-v',str(args.artifacts.resolve())+':/decoder:ro','--entrypoint','/decoder/unisoc-ble-decode',args.image],
        input=''.join(json.dumps(f)+'\n' for f in frames),text=True,capture_output=True,check=True,timeout=30)
    values=[json.loads(line) for line in result.stdout.splitlines()]
    assert values[0]['data']['tempc']==25,values
    assert values[0]['data']['hum']==50,values
    assert values[0]['data']['batt']==100,values
    assert values[1:]==[{},{}],values
    print('Actual ARM64 Theengs Decoder: ATC temperature/humidity/battery and unsupported-packet handling passed.')

if __name__=='__main__':main()
