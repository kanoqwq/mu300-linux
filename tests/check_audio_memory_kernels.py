#!/usr/bin/env python3
"""Compile the early FDT reservation using the existing prepared kernel(s)."""
import argparse
from pathlib import Path
import subprocess
import sys

TOP=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',type=Path,required=True)
    parser.add_argument('--versions',nargs='+',default=['6.18.54','7.2.8'])
    args=parser.parse_args()
    for version in args.versions:
        source=args.sources/('linux-'+version);objects=args.sources/('out-'+version)
        if not (source/'drivers/of/fdt.c').is_file() or not (objects/'.config').is_file():
            raise ValueError('Missing existing prepared kernel: '+version)
        subprocess.run([sys.executable,str(TOP/'upstream/port/install.py'),str(source)],check=True)
        subprocess.run(['make','-C',str(source),'O='+str(objects),'ARCH=arm64',
                        'CROSS_COMPILE=aarch64-linux-gnu-','-j4','drivers/of/fdt.o'],check=True)
        print('PASS:',version,'actual early-FDT object compiled; Image not rebuilt, no hardware accessed',flush=True)


if __name__=='__main__':main()
