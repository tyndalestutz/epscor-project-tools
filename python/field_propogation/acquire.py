#!/usr/bin/env python3
"""Measurement-first workflow: init, record, status, build, freeze, predict, compare.

This CLI records/imports manual instrument readings. It does not connect to or
command hardware. Follow docs/measurement_acquisition.md for the bench sequence.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    __package__='field_propogation'

from .configuration import ConfigurationError
from .acquisition.session import initialize, register, readiness, compact
from .acquisition.build import save_build
from .acquisition.prediction import freeze, predict, compare


def main():
    parser=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('init','status','compact'):
        sub=commands.add_parser(name)
        sub.add_argument('session',type=Path)
    sub=commands.add_parser('record'); sub.add_argument('session',type=Path)
    sub.add_argument('--step',required=True); sub.add_argument('--file',required=True,type=Path)
    for name in ('build','freeze'):
        sub=commands.add_parser(name);sub.add_argument('session',type=Path);sub.add_argument('--output',type=Path,required=True)
    sub=commands.add_parser('predict');sub.add_argument('bundle',type=Path);sub.add_argument('--output',type=Path,required=True)
    sub=commands.add_parser('compare');sub.add_argument('bundle',type=Path)
    sub.add_argument('--prediction',type=Path,required=True);sub.add_argument('--readings',type=Path,required=True);sub.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    try:
        if args.command=='init': initialize(args.session)
        elif args.command=='compact': compact(args.session)
        elif args.command=='record': print(register(args.session,args.step,args.file))
        elif args.command=='status':
            result=readiness(args.session);print(json.dumps(result,indent=2))
            if not result['complete']: parser.exit(1,'Acquisition is incomplete; no values were invented.\n')
        elif args.command=='build': save_build(args.session,args.output)
        elif args.command=='freeze': freeze(args.session,args.output)
        elif args.command=='predict': predict(args.bundle,args.output)
        elif args.command=='compare': print(json.dumps(compare(args.bundle,args.prediction,args.readings,args.output),indent=2))
    except (ConfigurationError,OSError,ValueError) as exc:
        parser.exit(2,f'Error: {exc}\n')


if __name__=='__main__': main()
