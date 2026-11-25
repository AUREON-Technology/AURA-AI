#!/usr/bin/python3 -I
"""Run a command with the kernel channels the network fence cannot see switched off.

    python3 -I confine.py -- <command> [argument]...

nftables filters IP. It does not filter AF_VSOCK, which reaches the machine hosting this VM, and
a seccomp filter on socket() can be stepped around through io_uring. Both are refused here, for
this process and everything it starts. Anything unexpected exits non-zero: the command never
runs unconfined.
"""

import ctypes
import os
import platform
import sys

AF_VSOCK = 40
EPERM, ENOSYS = 1, 38
PR_SET_SECCOMP, PR_SET_NO_NEW_PRIVS, SECCOMP_MODE_FILTER = 22, 38, 2
RET_ALLOW, RET_ERRNO = 0x7FFF0000, 0x00050000
LOAD, JUMP_EQUAL, JUMP_AT_LEAST, RETURN = 0x20, 0x15, 0x35, 0x06
NR_OFFSET, ARCH_OFFSET, FIRST_ARGUMENT_OFFSET = 0, 4, 16

# machine -> (audit architecture, socket, io_uring_setup, first number of a foreign ABI)
MACHINES = {
    "x86_64": (0xC000003E, 41, 425, 0x40000000),
    "aarch64": (0xC00000B7, 198, 425, 0x40000000),
}


class Instruction(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("jt", ctypes.c_uint8), ("jf", ctypes.c_uint8), ("k", ctypes.c_uint32)]


class Program(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint16), ("filter", ctypes.POINTER(Instruction))]


def confine() -> None:
    architecture, socket_call, uring_call, foreign = MACHINES[platform.machine()]
    # Jumps count from the following instruction.
    steps = [
        (LOAD, 0, 0, ARCH_OFFSET),                 # 0
        (JUMP_EQUAL, 0, 7, architecture),          # 1  another ABI -> 9
        (LOAD, 0, 0, NR_OFFSET),                   # 2
        (JUMP_AT_LEAST, 5, 0, foreign),            # 3  x32 numbers -> 9
        (JUMP_EQUAL, 5, 0, uring_call),            # 4  -> 10
        (JUMP_EQUAL, 0, 2, socket_call),           # 5  anything else -> 8
        (LOAD, 0, 0, FIRST_ARGUMENT_OFFSET),       # 6
        (JUMP_EQUAL, 1, 0, AF_VSOCK),              # 7  -> 9
        (RETURN, 0, 0, RET_ALLOW),                 # 8
        (RETURN, 0, 0, RET_ERRNO | EPERM),         # 9
        (RETURN, 0, 0, RET_ERRNO | ENOSYS),        # 10
    ]
    instructions = (Instruction * len(steps))(*[Instruction(*step) for step in steps])
    program = Program(len(steps), instructions)
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong]
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, None, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "no_new_privs")
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "seccomp")


def main() -> None:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        raise SystemExit(2)
    try:
        confine()
    except Exception as exc:  # refuse to run, whatever went wrong
        print(f"aura: cannot confine the command: {exc}", file=sys.stderr)
        raise SystemExit(94)
    command = sys.argv[2:]
    try:
        os.execvp(command[0], command)
    except OSError as exc:
        print(f"aura: {command[0]}: {exc.strerror}", file=sys.stderr)
        raise SystemExit(127)


if __name__ == "__main__":
    main()
