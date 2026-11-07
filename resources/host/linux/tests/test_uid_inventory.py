"""Synthetic protocol controls plus explicitly selected Linux kernel controls."""
import ctypes
import json
import os
from pathlib import Path
import subprocess
import stat
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import allocation_scope as scope
import uid_inventory as uid


def row(pid=19, user=7, group=8):
    return {"pid": pid, "start": 23, "uid": [user]*4, "gid": [group]*4}


def plan():
    return {"uid": 7, "gid": 8, "targets": [row()], "devices": [[1, 3]],
            "deadline": time.monotonic()+0.9}


class Protocol(unittest.TestCase):
    def test_plan_refuses_ambiguous_identity_and_unbounded_work(self):
        good = plan()
        self.assertEqual(good, uid.validate(good))
        for update in ({"uid": True}, {"gid": -1}, {"targets": []}, {"targets": [row()]*2},
                       {"targets": [row(user=9)]}, {"targets": [{**row(), "start": True}]},
                       {"devices": [[True, 3]]}, {"devices": [[1, 3]]*2},
                       {"deadline": float("nan")}, {"deadline": time.monotonic()+2},
                       {"deadline": time.monotonic()-1}):
            with self.subTest(update=update), self.assertRaises(uid.InventoryUnknown):
                uid.validate({**good, **update})

    def answer(self, **changes):
        return Mock(returncode=0, stderr=b"", stdout=json.dumps([{"identity": row(), "holder": True}]).encode()+b"\n", **changes)

    def test_response_and_process_birth_are_both_bound(self):
        with patch.object(uid, "identity", return_value=row()), patch.object(uid.subprocess, "run", return_value=self.answer()) as run:
            p = plan()
            self.assertEqual([19], uid.collect(**p))
            args, kwargs = run.call_args
            self.assertEqual([sys.executable, "-I", "-B", "-S", str(Path(uid.__file__).resolve())], args[0])
            self.assertEqual({}, kwargs["env"])
            self.assertTrue(kwargs["close_fds"])
            self.assertEqual("/", kwargs["cwd"])
            self.assertNotIn("preexec_fn", kwargs)
            self.assertEqual(p, json.loads(kwargs["input"]))
        for change in ({"start": 24}, {"uid": [9]*4}):
            with self.subTest(change=change), patch.object(uid, "identity", return_value={**row(), **change}), \
                 patch.object(uid.subprocess, "run", return_value=self.answer()), self.assertRaises(uid.InventoryUnknown):
                uid.collect(**plan())

    def test_no_partial_or_unbound_reply_is_accepted(self):
        for raw in (b"[]\n", b"not json\n", b"[]\n[]\n", b"x"*(uid.MAX_FRAME+1),
                    json.dumps([{"identity": row(), "holder": 1}]).encode()+b"\n",
                    json.dumps([{"identity": {**row(), "pid": 20}, "holder": False}]).encode()+b"\n"):
            answer = self.answer(); answer.stdout = raw
            with self.subTest(raw=raw[:50]), patch.object(uid.subprocess, "run", return_value=answer), \
                 self.assertRaises((uid.InventoryUnknown, ValueError)):
                uid.collect(**plan())
        with patch.object(uid.subprocess, "run", side_effect=subprocess.TimeoutExpired("collector", 1)), \
             self.assertRaises(subprocess.TimeoutExpired):
            uid.collect(**plan())

    def test_runtime_regular_fd_is_closed_but_device_alias_is_refused(self):
        pipe = Mock(st_mode=stat.S_IFIFO)
        regular = Mock(st_mode=stat.S_IFREG)
        with patch.object(uid.os, "listdir", return_value=["0", "1", "2", "6"]), \
             patch.object(uid.os, "fstat", side_effect=[pipe, pipe, pipe, regular]), patch.object(uid.os, "close") as close:
            uid.inherited()
            close.assert_called_once_with(6)
        with patch.object(uid.os, "listdir", return_value=["6"]), \
             patch.object(uid.os, "fstat", return_value=Mock(st_mode=stat.S_IFCHR)), self.assertRaises(uid.InventoryUnknown):
            uid.inherited()

    def test_foreign_uid_fallback_is_not_an_exclusion(self):
        expected = row()
        entry = Mock(name="19"); entry.name = "19"
        with patch.object(Path, "iterdir", return_value=iter([entry])), \
             patch.object(scope.native, "process", return_value={"start": 23}), \
             patch.object(scope, "_descriptor_holder", side_effect=PermissionError), \
             patch.object(scope.os, "geteuid", return_value=0), \
             patch.object(scope.uid_scan, "identity", return_value=expected), \
             patch.object(scope.uid_scan, "collect", return_value=[19]) as collect:
            # Each complete PID snapshot needs its own iterator.
            with patch.object(Path, "iterdir", side_effect=lambda: iter([entry])):
                self.assertEqual([19], scope.visible_holders({"fixture": [1, 3]}))
            self.assertEqual((7, 8, [expected], [[1, 3]]), collect.call_args.args[:4])
        # Synthetic proc roots can never dispatch a collector against real PIDs.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); (root/"19").mkdir()
            with patch.object(scope.native, "process", return_value={"start": 23}), \
                 patch.object(scope, "_descriptor_holder", side_effect=PermissionError), \
                 patch.object(scope.uid_scan, "collect") as collect, self.assertRaises(PermissionError):
                scope.visible_holders({"fixture": [1, 3]}, proc=root)
            collect.assert_not_called()

    def test_same_uid_or_mixed_credentials_remain_unknown(self):
        entry = Mock(); entry.name = "19"
        for expected in (row(user=0), {**row(), "uid": [7, 8, 7, 7]}):
            with self.subTest(expected=expected), patch.object(Path, "iterdir", return_value=iter([entry])), \
                 patch.object(scope.native, "process", return_value={"start": 23}), \
                 patch.object(scope, "_descriptor_holder", side_effect=PermissionError), \
                 patch.object(scope.os, "geteuid", return_value=0), \
                 patch.object(scope.uid_scan, "identity", return_value=expected), \
                 patch.object(scope.uid_scan, "collect") as collect, self.assertRaises(scope.native.NativeUnknown):
                scope.visible_holders({"fixture": [1, 3]})
            collect.assert_not_called()


TARGET = r"""
import ctypes,json,os,sys
lib=ctypes.CDLL(None,use_errno=True)
class H(ctypes.Structure):_fields_=[('version',ctypes.c_uint32),('pid',ctypes.c_int)]
class D(ctypes.Structure):_fields_=[('e',ctypes.c_uint32),('p',ctypes.c_uint32),('i',ctypes.c_uint32)]
if lib.capset(ctypes.byref(H(0x20080522,0)),ctypes.byref((D*2)())):raise RuntimeError('capability drop')
handle=os.open(os.devnull,os.O_RDONLY) if sys.argv[1]=='holder' else None
if lib.prctl(4,0 if sys.argv[1]=='protected' else 1,0,0,0):raise RuntimeError('dumpable')
print(json.dumps({'pid':os.getpid()}),flush=True)
sys.stdin.buffer.read(1)
"""


class Kernel(unittest.TestCase):
    def target(self, mode):
        process = subprocess.Popen([sys.executable, "-I", "-B", "-S", "-c", TARGET, mode],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   env={}, close_fds=True)
        self.addCleanup(self.finish, process)
        answer = json.loads(process.stdout.readline())
        self.assertEqual(process.pid, answer["pid"])
        return process

    def finish(self, process):
        if process.poll() is None:
            process.stdin.write(b"x"); process.stdin.flush()
        out, error = process.communicate(timeout=5)
        self.assertEqual(0, process.returncode, out+error)

    def collect(self, process, expected=None):
        info = os.stat(os.devnull)
        devices = [[os.major(info.st_rdev), os.minor(info.st_rdev)]]
        expected = expected or uid.identity(process.pid)
        return uid.collect(os.getuid(), os.getgid(), [expected], devices, time.monotonic()+0.9)

    def test_real_fd_alias_is_visible_and_idle_target_is_empty(self):
        holder, idle = self.target("holder"), self.target("idle")
        self.assertEqual([holder.pid], self.collect(holder))
        self.assertEqual([], self.collect(idle))

    def test_real_protected_target_and_wrong_birth_cannot_be_clean(self):
        protected, idle = self.target("protected"), self.target("idle")
        with self.assertRaises(uid.InventoryUnknown):
            self.collect(protected)
        expected = uid.identity(idle.pid); expected["start"] += 1
        with self.assertRaises(uid.InventoryUnknown):
            self.collect(idle, expected)

    def test_inherited_device_alias_is_refused_before_scan(self):
        process = self.target("idle")
        descriptor = os.open(os.devnull, os.O_RDONLY)
        try:
            p = plan(); p.update(uid=os.getuid(), gid=os.getgid(), targets=[uid.identity(process.pid)])
            answer = subprocess.run([sys.executable, "-I", "-B", "-S", uid.__file__],
                input=json.dumps(p).encode()+b"\n", capture_output=True, pass_fds=(descriptor,), env={}, timeout=2)
        finally:
            os.close(descriptor)
        self.assertEqual(1, answer.returncode)
        self.assertEqual(b"", answer.stdout)
        self.assertEqual(b"", answer.stderr)


if __name__ == "__main__":
    unittest.main()
