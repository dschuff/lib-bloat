#!/usr/bin/env python3

import os
from pathlib import Path
import subprocess
import sys

LLVM_DIR = Path('/s/emr/install/bin')
BLOATY_DIR = Path.home() / 'software' / 'bloaty'

def run_tool(cmd):
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode:
        print(f'Command Failed:')
        print(''.join([str(p) for p in cmd]))
        print(result.stdout.decode())
        print(result.stderr.decode())
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result.stdout.decode()

def GetLibFunctions(libs):
    nm = LLVM_DIR / 'llvm-nm'
    func_names = set()
    for lib in libs:
        nm_output = run_tool([nm, lib])
        for line in nm_output.split('\n'):
            try:
                addr, symtype, name = line.split()
                #print(f'type {symtype}, name {name}')
                if symtype.lower() == 't': # A global or local defined function sym
                    func_names.add(name)
            except ValueError: # fewer than 3 tokens
                continue
        print(f'{len(func_names)} functions in {lib}')
    return func_names

def GetFuncSizes(wasm):
    bloaty = BLOATY_DIR / 'bloaty'
    bloaty_output = run_tool([bloaty, '-d', 'symbols', '-n', '0', '--csv', wasm])
    func_sizes = {}
    for line in bloaty_output.split('\n'):
        if (line.startswith('[section') or line.endswith('filesize') or
            line.startswith('[WASM Header') or len(line) == 0):
            continue
        name, vmsize, filesize = line.split(',')
        func_sizes[name] = int(filesize)
    print(f'{len(func_sizes)} functions in {wasm}')
    return func_sizes

def main(args):
    libs = args[:-1]
    linked_wasm = args[-1]
    lib_funcs = GetLibFunctions(libs)
    func_sizes = GetFuncSizes(linked_wasm)

    lib_size = 0
    total_func_size = 0
    for func, size in func_sizes.items():
        total_func_size += size
        if func in lib_funcs:
            lib_size += size
    percent = lib_size / total_func_size * 100
    print(f'Total lib size: {lib_size:,} of {total_func_size:,} bytes ({percent:.1f}%)')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
