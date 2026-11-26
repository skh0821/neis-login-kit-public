# -*- coding: utf-8 -*-
"""다른 프로세스의 메모리에서 «가상 비밀번호»가 몇 군데 남았는지 «개수만» 센다 (시험 전용).

메모리 내용은 어디에도 저장·출력하지 않는다. 진짜 비밀번호에는 쓰지 않는다 (가상 값으로만).
"""
import ctypes
from ctypes import wintypes

PROCESS_QUERY_INFORMATION, PROCESS_VM_READ = 0x0400, 0x0010
MEM_COMMIT = 0x1000
PAGE_NOACCESS, PAGE_GUARD = 0x01, 0x100


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p), ("AllocationProtect", wintypes.DWORD),
                ("PartitionId", wintypes.WORD), ("RegionSize", ctypes.c_size_t), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD)]


def count_in_process(pid: int, patterns: list[bytes]) -> dict:
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = wintypes.HANDLE
    k.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
    k.VirtualQueryEx.restype = ctypes.c_size_t
    k.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                    ctypes.POINTER(ctypes.c_size_t)]
    h = k.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not h:
        raise OSError("OpenProcess failed")
    counts = {i: 0 for i in range(len(patterns))}
    chunk = 4 * 1024 * 1024
    buf = ctypes.create_string_buffer(chunk)
    overlap = max(len(p) for p in patterns)
    addr, scanned = 0, 0
    mbi = MBI()
    try:
        while addr < 0x7FFFFFFF0000 and k.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            base, size = mbi.BaseAddress or 0, mbi.RegionSize
            if mbi.State == MEM_COMMIT and not (mbi.Protect & (PAGE_NOACCESS | PAGE_GUARD)) and mbi.Protect:
                off, tail = 0, b""
                while off < size:
                    n = min(chunk, size - off)
                    got = ctypes.c_size_t(0)
                    if k.ReadProcessMemory(h, ctypes.c_void_p(base + off), buf, n, ctypes.byref(got)) and got.value:
                        data = tail + buf.raw[:got.value]
                        for i, p in enumerate(patterns):
                            counts[i] += data.count(p)
                        tail = data[-(overlap - 1):] if overlap > 1 else b""
                        scanned += got.value
                    else:
                        tail = b""
                    off += n
            addr = base + size
    finally:
        k.CloseHandle(h)
    return {"hits": [counts[i] for i in range(len(patterns))], "scanned_mb": round(scanned / 1048576)}
