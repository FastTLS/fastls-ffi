"""Windows benchmark process-tree observation without per-process system queries.

Windows process ancestry and thread counts come from one documented Tool Help
process snapshot. CPU, RSS and handles retain the existing psutil counters.
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/ns-tlhelp32-processentry32w
https://learn.microsoft.com/en-us/windows/win32/api/tlhelp32/nf-tlhelp32-createtoolhelp32snapshot
"""
import ctypes
from functools import lru_cache
import os
import time
import psutil


class ProcessEntry(ctypes.Structure):
    _fields_ = [('dwSize', ctypes.c_uint32), ('cntUsage', ctypes.c_uint32),
        ('th32ProcessID', ctypes.c_uint32), ('th32DefaultHeapID', ctypes.c_size_t),
        ('th32ModuleID', ctypes.c_uint32), ('cntThreads', ctypes.c_uint32),
        ('th32ParentProcessID', ctypes.c_uint32), ('pcPriClassBase', ctypes.c_int32),
        ('dwFlags', ctypes.c_uint32), ('szExeFile', ctypes.c_uint16 * 260)]


@lru_cache(maxsize=1)
def _api():
    if os.name != 'nt':
        raise RuntimeError('Tool Help process snapshots require Windows')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    api.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    for name in ('Process32FirstW', 'Process32NextW'):
        method = getattr(api, name)
        method.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessEntry)]
        method.restype = ctypes.c_int32
    api.CloseHandle.argtypes = [ctypes.c_void_p]
    api.CloseHandle.restype = ctypes.c_int32
    return api


def process_entries():
    api = _api()
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        entry = ProcessEntry(); entry.dwSize = ctypes.sizeof(entry)
        present = api.Process32FirstW(handle, ctypes.byref(entry))
        rows = {}
        while present:
            rows[entry.th32ProcessID] = dict(parent=entry.th32ParentProcessID,
                                           threads=entry.cntThreads)
            entry.dwSize = ctypes.sizeof(entry)
            present = api.Process32NextW(handle, ctypes.byref(entry))
        error = ctypes.get_last_error()
        if error != 18:
            raise ctypes.WinError(error)
        return rows
    finally:
        if not api.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def process_tree(root, entries=None, process_factory=psutil.Process):
    entries = process_entries() if entries is None else entries
    if root.pid not in entries or not root.is_running():
        raise psutil.NoSuchProcess(root.pid)
    children = {}
    for pid, entry in entries.items():
        children.setdefault(entry['parent'], []).append(pid)
    found = [(root, entries[root.pid]['threads'])]
    seen = {root.pid}
    for parent, _ in found:
        try:
            born = parent.create_time()
        except psutil.NoSuchProcess:
            if parent is root:raise
            continue
        for pid in children.get(parent.pid, []):
            if pid in seen:continue
            seen.add(pid)
            try:
                child = process_factory(pid)
                # Windows retains an orphan's original PPID. Exclude children
                # of an earlier process that used this parent's recycled PID.
                if child.create_time() >= born and child.is_running():
                    found.append((child, entries[pid]['threads']))
            except psutil.NoSuchProcess:
                continue
    return found


def snapshot(root):
    members = []
    for member, threads in process_tree(root):
        try:
            with member.oneshot():
                cpu = member.cpu_times()
                row = dict(pid=member.pid, cpu_seconds=cpu.user + cpu.system,
                    rss_bytes=member.memory_info().rss, threads=threads,
                    descriptors=member.num_handles())
            if member.is_running():members.append(row)
        except psutil.NoSuchProcess:
            continue
    if not members or not root.is_running():raise psutil.NoSuchProcess(root.pid)
    return dict(at=time.perf_counter(), processes=members,
        **{key:sum(m[key] for m in members) for key in
           ('cpu_seconds','rss_bytes','threads','descriptors')})
