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
import operator
import os
from pathlib import Path
import subprocess
import sys

LLVM_DIR = Path('/s/emr/install/bin')
BLOATY_DIR = Path.home() / 'software' / 'bloaty'
VERBOSE = False

tool_output_cache = {}
def run_tool(cmd):
    cmd_str = repr(cmd)
    if cmd_str in tool_output_cache:
        return tool_output_cache[cmd_str]
    if VERBOSE:
        print(' '.join([str(p) for p in cmd]))

    result = subprocess.run(cmd, capture_output=True)
    if result.returncode:
        print(f'Command Failed:')
        print(' '.join([str(p) for p in cmd]))
        print(result.stdout.decode())
        print(result.stderr.decode())
        raise subprocess.CalledProcessError(result.returncode, cmd)
    result_text = result.stdout.decode()
    tool_output_cache[cmd_str] = result_text
    return result_text


def GetSymSizes(wasm):
    bloaty = BLOATY_DIR / 'bloaty'
    bloaty_output = run_tool([bloaty, '-d', 'symbols', '-n', '0',
                              '--demangle=none', '--csv', wasm])
    sym_sizes = {}
    total_size = 0
    for line in bloaty_output.split('\n'):
        #print(line)
        if (line.startswith('[section') or line.endswith('filesize') or
            line.startswith('[WASM Header') or len(line) == 0):
            continue
        #print(line)
        name, vmsize, filesize = line.split(',')
        total_size += int(filesize)
        sym_sizes[name] = int(filesize)

    print(f'{len(sym_sizes)} symbols in {wasm} ({total_size:,} bytes)')
    return sym_sizes


def GetLibFunctions(libs):
    nm = LLVM_DIR / 'llvm-nm'
    func_names = set()
    weak_names = set()
    data_names = {}
    local_data_names = set()
    for lib in libs:
        functions = 0
        weaks = 0
        datas = 0
        local_datas = 0
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
                elif symtype.lower() == 'd':
                    #if name in data_names and not name.startswith('.L') and  not 'piecewise_construct' in name:
                    #    print(f'Warning: duplicate data name {name} in {lib} and {data_names[name]}')
                    if name.startswith('.L'):
                        local_data_names.add(name)
                        local_datas += 1
                    else:
                        data_names[name] = lib
                        datas += 1
            except ValueError: # fewer than 3 tokens
                continue
        if VERBOSE:
            print(f'{functions} functions, {weaks} weak symbols, and {data} data symbols in {lib}')
    return func_names, weak_names, data_names, local_data_names


LibSize = namedtuple('LibSize', ['name', 'function', 'weak', 'data', 'local'])

def GetLibSize(libs, sym_sizes):
    lib_size = 0
    lib_weak_size = 0
    lib_data_size = 0
    local_data_size = 0
    lib_funcs, lib_weaks, lib_datas, lib_local_datas = GetLibFunctions(libs)
    for sym, size in sym_sizes.items():
        if sym in lib_funcs:
            lib_size += size
        elif sym in lib_weaks:
            lib_weak_size += size
        if sym.startswith('.rodata') or sym.startswith('.data'):
            stripped_name = sym.removeprefix('.rodata.').removeprefix('.data.')
            if stripped_name in lib_datas:
                lib_data_size += size
            elif stripped_name.startswith('.L'):
                local_data_size += size
    name = libs[0].name if len(libs) == 1 else '(aggregate)'
    return LibSize(name, lib_size, lib_weak_size, lib_data_size, local_data_size)


def GetDataSize(sym_sizes):
    data_sym_count = 0
    data_size = 0
    for name, size in sym_sizes.items():
        if name == '.data' or name == '.rodata':
            print(f'Warning: wasm file seems to have a single merged {name}'
                  f'section of size {size}')
        elif (name.startswith('.data')
              or name.startswith('.rodata')
              or name.startswith('.tdata')):
            data_sym_count += 1
            data_size += size
    return data_sym_count, data_size

def main(args):
    libs = [Path(f) for f in args[:-1]]
    linked_wasm = Path(args[-1])

    sym_sizes = GetSymSizes(linked_wasm)
    linked_sym_size = sum(size for sym, size in sym_sizes.items())
    data_sym_count, data_sym_size = GetDataSize(sym_sizes)
    #print(f'Total symbols size in {linked_wasm}: {linked_sym_size:,}')
    print(f'{data_sym_count} data symbols ({data_sym_size:,} bytes)')

    sizes = []

    for lib in libs:
        sizes.append(GetLibSize([lib], sym_sizes))
    sizes.sort(key=lambda i: i.function + i.weak, reverse=True)

    def Percent(s):
        return s / linked_sym_size * 100

    print(' ' * 62 + 'Functions' + ' ' * 4 + '(Functions + weak syms)')
    print(f'{"Name":50}' + '      size        pct       size        pct')
    for lib in sizes:
        print(f'{lib.name:50}{lib.function:10,}{Percent(lib.function):10.1f}%\t', end='')
        combined = lib.function + lib.weak
        print(f'{combined:10,}{Percent(combined):10.1f}%')


    sizes.sort(key=operator.attrgetter('data'), reverse=True)
    print(' ' * 62 + 'Data')
    print(f'{"Name":50}' + '      size        pct (of all data)')
    for lib in sizes:
        print(f'{lib.name:50}{lib.data:10,}{lib.data / data_sym_size*100:10.1f}%')


    libs_size_sum = sum(lib.function for lib in sizes)
    # To calculate weak symbols properly, we want each weak symbol to be counted
    # only once globally, rather than once per input/library. So, run the calculation
    # again, but with all of the inputs together (which deduplicates all symbols).
    # It seems to happen in some
    # cases that the sum of the strongly-defined function sizes in the libs is
    # also larger than the deduplicated total (this shouldn't be true if the inputs
    # are object files rather than archives, and are all included in the link,
    # as this would result in a multiple definition error). Warn in this case.
    # Probably there is some inaccuracy in this script, or perhaps some object
    # file was generated but not actually included in the link.
    deduped_size = GetLibSize(libs, sym_sizes)
    if libs_size_sum != deduped_size.function:
        print(f'warning: sum of strong definition sizes from all inputs is {libs_size_sum}, deduplicated total is {deduped_size.function}')
    #assert total_size.function == libs_size
    deduped_weak_size = deduped_size.function + deduped_size.weak
    #libs_weak_size = sum(lib.function + lib.weak for lib in sizes)
    libs_data_size = sum(lib.data for lib in sizes)

    print(f'Total size covered by strong functions in libs: {deduped_size.function:,} of {linked_sym_size:,} bytes ({Percent(deduped_size.function):.1f}%)')
    print(f'Total size covered by strong and weak functions in libs: {deduped_weak_size:,} of {linked_sym_size:,} bytes ({Percent(deduped_weak_size):.1f}%)')
    print(f'Total size covered by public data in libs: {libs_data_size:,} of {data_sym_size:,} bytes ({Percent(libs_data_size):.1f}% of total symbols, {libs_data_size/data_sym_size* 100:.1f}% of data section)')
    local_data = sizes[0].local
    print(f'Total size covered by local data (not attributable to libs): {local_data:,} of {data_sym_size:,} bytes ({Percent(local_data):.1f}% of total symbols, {local_data/data_sym_size*100:.1f}% of data section)')

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
