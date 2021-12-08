#!/usr/bin/env python3

# USAGE: python3 lib_bloat.py <object/archive> <object/archive> ... <linked wasm>

# NOTE: For this to work reliably, the linked wasm file needs a name section,
# with mangled names. That means linking with -g or --profiling-funcs, and with
# -Wl,--no-demangle

# This script tracks weak symbols separately from defined functions. This is
# weak symbols are not attributable to only one object file, and removing
# the entire object from the link would not remove the weak symbol from the
# final linked output unless that object is the only one defining the symbol.

from collections import namedtuple
import os
from pathlib import Path
import subprocess
import sys

LLVM_DIR = Path('/s/emr/install/bin')
BLOATY_DIR = Path.home() / 'software' / 'bloaty'

def run_tool(cmd):
    #print(' '.join([str(p) for p in cmd]))
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode:
        print(f'Command Failed:')
        print(' '.join([str(p) for p in cmd]))
        print(result.stdout.decode())
        print(result.stderr.decode())
        raise subprocess.CalledProcessError(result.returncode, cmd)
    return result.stdout.decode()


def PrintLibSize(lib, size, weak, total):
    percent = size / total * 100
    print(f'Total lib size: {size:,} of {total:,} bytes ({percent:.1f}%)')
    percent = (size + weak) / total * 100
    print(f'Total lib size (including weak): {size + weak:,} of {total:,} bytes ({percent:.1f}%)')


def GetLibFunctions(libs):
    nm = LLVM_DIR / 'llvm-nm'
    func_names = set()
    weak_names = set()
    for lib in libs:
        functions = 0
        weaks = 0
        nm_output = run_tool([nm, lib])
        for line in nm_output.split('\n'):
            try:
                addr, symtype, name = line.split()
                #print(f'type {symtype}, name {name}')
                if symtype.lower() == 't': # A global or local defined function sym
                    func_names.add(name)
                    functions += 1
                elif symtype.lower() == 'w': # A global or local weak symbol
                    weak_names.add(name)
                    weaks += 1
            except ValueError: # fewer than 3 tokens
                continue
    print(f'{functions} functions and {weaks} weak symbols in {lib}')
    return func_names, weak_names

def GetFuncSizes(wasm):
    bloaty = BLOATY_DIR / 'bloaty'
    bloaty_output = run_tool([bloaty, '-d', 'symbols', '-n', '0',
                              '--demangle=none', '--csv', wasm])
    func_sizes = {}
    total_size = 0
    for line in bloaty_output.split('\n'):
        #print(line)
        if (line.startswith('[section') or line.endswith('filesize') or
            line.startswith('[WASM Header') or len(line) == 0):
            continue
        #print(line)
        name, vmsize, filesize = line.split(',')
        total_size += int(filesize)
        func_sizes[name] = int(filesize)

    print(f'{len(func_sizes)} functions in {wasm} ({total_size:,} bytes)')
    return func_sizes


LibSize = namedtuple('LibSize', ['name', 'function', 'weak'])

def GetLibSize(lib, func_sizes):
    lib_size = 0
    lib_weak_size = 0
    lib_funcs, lib_weaks = GetLibFunctions([lib])
    for func, size in func_sizes.items():
        if func in lib_funcs:
            lib_size += size
        elif func in lib_weaks:
            lib_weak_size += size
    return LibSize(lib.name, lib_size, lib_weak_size)

def main(args):
    libs = [Path(f) for f in args[:-1]]
    linked_wasm = Path(args[-1])

    func_sizes = GetFuncSizes(linked_wasm)
    total_func_size = sum(size for func, size in func_sizes.items())
    #print(f'Total functions size in {linked_wasm}: {total_func_size:,}')

    sizes = []

    for lib in libs:
        sizes.append(GetLibSize(lib, func_sizes))
    sizes.sort(key=lambda i: i.function + i.weak, reverse=True)

    def Percent(s):
        return s / total_func_size * 100

    print(' ' * 62 + 'Functions' + ' ' * 4 + '(Functions + weak syms)')
    print(f'{"Name":50}' + '      size        pct       size        pct')
    for lib in sizes:
        print(f'{lib.name:50}{lib.function:10,}{Percent(lib.function):10.1f}%\t', end='')
        combined = lib.function + lib.weak
        print(f'{combined:10,}{Percent(combined):10.1f}%')


    libs_size = sum(lib.function for lib in sizes)
    libs_weak_size = sum(lib.function + lib.weak for lib in sizes)

    print(f'Total lib size: {libs_size:,} of {total_func_size:,} bytes ({Percent(libs_size):.1f}%)')
    print(f'Total lib size (including weak): {libs_weak_size:,} of {total_func_size:,} bytes ({Percent(libs_weak_size):.1f}%)')


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
