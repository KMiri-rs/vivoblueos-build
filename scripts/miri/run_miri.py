#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Copyright (c) 2025 vivo Mobile Communication Co., Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import subprocess
import sys


def get_all_deps(target, out_dir, cur_cwd):
    result = subprocess.run(
        ["gn", "desc", out_dir, target, "deps", "--all"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cur_cwd,
    )
    if result.returncode != 0:
        sys.stderr.write(
            f"gn desc failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
        )
        sys.exit(result.returncode)
    return [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip().startswith("//") and ":" in line
    ]


def label_to_ninja_path(label, abs_out_dir):
    # //kernel/infra:blueos_infra -> <abs_out_dir>/obj/kernel/infra/blueos_infra.ninja
    path_part, _, name_part = label.lstrip("/").partition(":")
    return os.path.join(abs_out_dir, "obj", path_part, f"{name_part}.ninja")


def read_ninja_externs_and_rustdeps(ninja_path, out_dir):
    """Read externs and rustdeps from a .ninja file.

    Returns (externs_tokens, rustdeps_tokens) with paths prefixed by out_dir
    so they are relative to project_root rather than out_dir.
    """
    raw_externs = []
    raw_rustdeps = []
    try:
        with open(ninja_path) as f:
            for line in f:
                s = line.strip()
                if s.startswith("externs = "):
                    raw_externs = s[len("externs = "):].split()
                elif s.startswith("rustdeps = "):
                    raw_rustdeps = s[len("rustdeps = "):].split()
    except FileNotFoundError:
        return [], []

    externs = _prefix_extern_paths(raw_externs, out_dir)
    rustdeps = _prefix_ldep_paths(raw_rustdeps, out_dir)
    return externs, rustdeps


def _prefix_extern_paths(tokens, out_dir):
    result = []
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t == "--extern" and i + 1 < len(tokens):
            result.append(t)
            i += 1
            name, _, path = tokens[i].partition("=")
            result.append(f"{name}={os.path.join(out_dir, path)}")
        else:
            result.append(t)
        i += 1
    return result


def _prefix_ldep_paths(tokens, out_dir):
    result = []
    for t in tokens:
        if t.startswith("-Ldependency="):
            path = t[len("-Ldependency="):]
            result.append(f"-Ldependency={os.path.join(out_dir, path)}")
        else:
            result.append(t)
    return result


def collect_flags_from_deps(deps, abs_out_dir, out_dir):
    """Gather --extern and -Ldependency flags from every dep's ninja file.

    Reading the ninja file gives us the *real* extern names (including aliased
    crate names like lock_api_crate) that were used at compile time, which is
    exactly what miri needs when it loads those rlibs.
    """
    all_externs = {}   # extern_name -> prefixed_path  (last writer wins)
    all_ldeps = set()

    for dep in deps:
        ninja_path = label_to_ninja_path(dep, abs_out_dir)
        externs_tokens, rustdeps_tokens = read_ninja_externs_and_rustdeps(
            ninja_path, out_dir
        )

        i = 0
        while i < len(externs_tokens):
            if externs_tokens[i] == "--extern" and i + 1 < len(externs_tokens):
                name, _, path = externs_tokens[i + 1].partition("=")
                all_externs[name] = path
                i += 2
            else:
                i += 1

        for r in rustdeps_tokens:
            all_ldeps.add(r)

    extern_flags = []
    for name, path in all_externs.items():
        extern_flags.extend(["--extern", f"{name}={path}"])

    return extern_flags, sorted(all_ldeps)


def main():
    if len(sys.argv) < 5:
        sys.stderr.write(
            "Usage: script.py <target> <outdir> <project_root> <path_to_lib.rs> [extra miri args]\n"
        )
        return 1

    target = sys.argv[1]
    out_dir = sys.argv[2]        # e.g. "out/none"  (relative to project_root)
    project_root = sys.argv[3]   # e.g. "/blueos-dev/"
    lib_path = sys.argv[4]

    abs_out_dir = os.path.join(project_root, out_dir)

    try:
        deps = get_all_deps(target, out_dir, project_root)
        extern_flags, ldep_flags = collect_flags_from_deps(deps, abs_out_dir, out_dir)

        command_and_args = (
            ["miri", "--edition=2021", "--test", lib_path]
            + extern_flags
            + ldep_flags
            + sys.argv[5:]
        )

        print(f"Executing command: {' '.join(command_and_args)}")

        process = subprocess.Popen(
            command_and_args, env=os.environ, cwd=project_root
        )
        process.wait()

        if process.returncode != 0:
            sys.stderr.write(
                f"\nCommand failed with exit code {process.returncode}\n"
            )
            sys.exit(process.returncode)

    except FileNotFoundError as e:
        sys.stderr.write(
            f"Error: command not found ({e}). Ensure gn and miri are in PATH.\n"
        )
        sys.exit(1)
    except Exception as e:
        sys.stderr.write(f"An unexpected error occurred: {e}\n")
        sys.exit(1)


if __name__ == "__main__":
    sys.exit(main())
