#!/usr/bin/env python3
"""Launch the BlueOS kernel crate under Miri with GN's own compile flags.

This is a plain launcher, not a checker: it reconstructs the rustc command GN
generated for the kernel rlib, repoints it at the Miri interpreter, and runs it.
The kernel decides its own outcome through its assertions and interpreter_exit,
which is surfaced to ninja purely as Miri's exit code plus the stamp file.
"""
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

def main():
    root = Path(__file__).resolve().parents[3]
    out, kmiri_toml, stamp = [
        (root / path).resolve() for path in sys.argv[1:4]
    ]
    miri = shutil.which('miri')
    if miri is None:
        raise SystemExit('miri not found on PATH')
    sysroot = os.environ['MIRI_BOOT_SYSROOT']

    # Reuse the exact rustc command GN generated for the kernel crate so Miri
    # interprets the same flags, cfgs and dependency rlibs as the real build.
    raw = subprocess.check_output(
        ['ninja', '-C', str(out), '-t', 'commands',
         'obj/kernel/kernel/blueos/libblueos.rlib'],
        text=True).splitlines()[-1]
    tokens = shlex.split(raw.split(' && ', 1)[0])

    # Keep the leading env assignments (rustenv) for the interpreted crate: the
    # board proc-macro reads TARGET_BOARD to generate the boards module.
    env = dict(os.environ)
    while tokens and '=' in tokens[0] and not tokens[0].startswith('-'):
        name, value = tokens.pop(0).split('=', 1)
        env[name] = value
    if Path(tokens.pop(0)).name != 'rustc':
        raise SystemExit('unexpected GN compiler command')

    # Keep every flag; only force a bin crate so Miri interprets via miri_start.
    args = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == '-o':
            i += 2
        elif token.startswith('--emit='):
            i += 1
        elif token == '--crate-type':
            args += [token, 'bin']
            i += 2
        else:
            args.append(token)
            i += 1

    command = [
        str(miri), *args,
        '--cfg=blueos_miri_empty_init_array',
        '-Zkmiri-toml=' + str(kmiri_toml),
        '-Zmiri-backtrace=full',
    ]
    env['MIRI_SYSROOT'] = str(sysroot)
    print('Boot command:', shlex.join(command), flush=True)
    try:
        result = subprocess.run(command, cwd=out, env=env, timeout=600)
    except subprocess.TimeoutExpired:
        return 124
    if result.returncode == 0:
        stamp.write_text('BLUEOS_MIRI_DONE\n')
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
