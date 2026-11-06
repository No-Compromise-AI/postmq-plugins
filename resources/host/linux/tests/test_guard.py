"""CPU-only guard checks; these do not qualify a native systemd fixture."""

from pathlib import Path
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import guard

CLAIM = "01AAAAAAAAAAAAAAAAAAAAAA01"


class GuardTests(unittest.TestCase):
    def test_killed_before_release_is_not_an_application_signal(self):
        helper=str(Path(guard.__file__).resolve().parent)
        script='''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import guard
leader=os.getpid();original_write=os.write
def write(fd,data):
    count=original_write(fd,data)
    if os.getpid()==leader and data==b'x':Path(sys.argv[3]).touch()
    return count
guard.os.write=write
status=guard.launch_job(['/bin/true'],int(sys.argv[4]),lambda pid:Path(sys.argv[2]).write_text(str(pid)))
sys.exit(0 if status is None else 1)
'''
        with tempfile.TemporaryDirectory() as temp:
            pidfile,placed=Path(temp)/'pid',Path(temp)/'placed'
            reader,writer=os.pipe();release=os.fdopen(writer,'wb',buffering=0)
            producer=subprocess.Popen([sys.executable,'-I','-c',script,helper,str(pidfile),str(placed),str(reader)],
                                      pass_fds=(reader,),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            os.close(reader)
            try:
                deadline=time.monotonic()+3
                while not placed.exists() and producer.poll() is None and time.monotonic()<deadline:time.sleep(0.01)
                self.assertTrue(placed.exists())
                os.kill(int(pidfile.read_text()),signal.SIGKILL)  # only this test's unreleased fork
                self.assertEqual(0,producer.wait(timeout=5))
                self.assertEqual(b'',producer.stderr.read())
            finally:
                release.close()
                if producer.poll() is None:
                    try:producer.wait(timeout=1)
                    except subprocess.TimeoutExpired:producer.kill()
                producer.wait(timeout=5);producer.stderr.close()

    def test_publication_decides_retention_after_sdk_process_loss(self):
        helper = str(Path(guard.__file__).resolve().parent)
        script = """
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1]);import guard
prepared=Path(sys.argv[2]);scope=prepared.parent
guard.subreaper=lambda:None
guard.current_scope=lambda *args:scope
guard.create_leaves=lambda *args:(scope/'guard',scope/'job')
guard.write_control=lambda path,pid:prepared.write_text(str(pid))
original_hold=guard.hold_evidence
def hold():
    (scope/'held').touch();original_hold()
guard.hold_evidence=hold
sys.exit(guard.main(sys.argv[3:]))
"""
        for published in (False, True):
            with self.subTest(published=published), tempfile.TemporaryDirectory() as temp:
                prepared, forbidden = Path(temp)/'prepared', Path(temp)/'forbidden'
                rr,rw=os.pipe();sr,sw=os.pipe();pr,pw=os.pipe()
                release=os.fdopen(rw,'wb',buffering=0);status=os.fdopen(sr,'rb',buffering=0)
                publication=os.fdopen(pw,'wb',buffering=0)
                child=subprocess.Popen([sys.executable,'-I','-c',script,helper,str(prepared),str(rr),str(sw),
                                        '--claim',CLAIM,'--fence','7','--publish-fd',str(pr),'--',
                                        sys.executable,'-I','-c',
                                        'from pathlib import Path; import sys; Path(sys.argv[1]).touch()',str(forbidden)],
                                       pass_fds=(rr,sw,pr),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                for fd in (rr,sw,pr):os.close(fd)
                try:
                    deadline=time.monotonic()+3
                    while not prepared.exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(0.01)
                    self.assertTrue(prepared.exists())
                    if published:publication.write(b'p')
                    publication.close();status.close();release.close()
                    if published:
                        deadline=time.monotonic()+3
                        while not (Path(temp)/'held').exists() and child.poll() is None and time.monotonic()<deadline:time.sleep(0.01)
                        self.assertTrue((Path(temp)/'held').exists())
                        self.assertIsNone(child.poll())
                    else:self.assertEqual(125,child.wait(timeout=5))
                    self.assertFalse(forbidden.exists())
                finally:
                    status.close();release.close();publication.close()
                    if child.poll() is None:child.kill()
                    child.wait(timeout=5);self.assertEqual(b'',child.stderr.read());child.stderr.close()

    def test_exec_failure_is_not_a_real_job_exit_126(self):
        helper=str(Path(guard.__file__).resolve().parent)
        script='''
import os,sys
sys.path.insert(0,sys.argv[1]);import guard
r,w=os.pipe();os.write(w,b'x');os.close(w)
try:
    status=guard.launch_job(['/missing-private-argv'] if sys.argv[2]=='missing' else ['/bin/sh','-c','exit 126'],r,lambda pid:None)
except RuntimeError:
    sys.exit(0 if sys.argv[2]=='missing' else 1)
sys.exit(0 if sys.argv[2]!='missing' and os.waitstatus_to_exitcode(status)==126 else 1)
'''
        for mode in ('missing','real126'):
            result=subprocess.run([sys.executable,'-I','-c',script,helper,mode],capture_output=True,timeout=5)
            self.assertEqual(0,result.returncode,result.stderr.decode())
            self.assertEqual(b'',result.stderr)

    def test_cli_separates_helper_options_from_job_arguments(self):
        options=['5','7','--claim',CLAIM,'--fence','7','--publish-fd','9']
        page=os.sysconf('SC_PAGE_SIZE')
        args=guard.parse_cli(options+['--memory-max',str(3*page),'--','app','--memory-max','job-value'])
        self.assertEqual(3*page,args.memory_max)
        self.assertEqual(['app','--memory-max','job-value'],args.command)
        invalid=[['5','5']+options[2:]+['--','app'],['2','7']+options[2:]+['--','app'],['5','7'],
                 options+['--'],['secret-value','7']+options[2:]+['--','app'],options+['--mem','5','--','app'],
                 options+['--memory-max',str(page),'--memory-max',str(2*page),'--','app'],options+['--',''],
                 options+['--memory-max','0','--','app'],options+['--memory-max','-1','--','app'],
                 options+['--memory-max',str(page+1),'--','app'],options+['--publish-fd','10','--','app'],
                 ['5','7','--claim',CLAIM,'--fence','7','--publish-fd','5','--','app']]
        for argv in invalid:
            with self.assertRaises(ValueError) as caught:guard.parse_cli(argv)
            self.assertNotIn('secret-value',str(caught.exception))

    def test_leaf_creation_moves_guard_before_enabling_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            scope = Path(temp)
            writes = []
            def writer(path, value):
                writes.append((str(path.relative_to(scope)), value))
                if str(path.relative_to(scope)) == "cgroup.subtree_control":
                    self.assertTrue((scope / "guard").is_dir())
                    self.assertFalse((scope / "job").exists())
            guard.create_leaves(scope, memory_max=3*os.sysconf("SC_PAGE_SIZE"), writer=writer, pid=42)
            self.assertEqual([("guard/cgroup.procs", 42), ("cgroup.subtree_control", "+memory"),
                              ("job/memory.max", 3*os.sysconf("SC_PAGE_SIZE"))], writes)

    def test_existing_guard_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as temp:
            (Path(temp) / "guard").mkdir()
            with self.assertRaises(FileExistsError):
                guard.create_leaves(Path(temp), writer=lambda *_: self.fail("must not write"))

    def test_status_pipe_reports_exit_and_signal(self):
        for status, expected in [(7 << 8, b"exit 7\n"), (9, b"signal 9\n")]:
            read_fd, write_fd = os.pipe()
            try:
                guard.report_status(write_fd, status)
                self.assertEqual(expected, os.read(read_fd, 100))
            finally:
                os.close(read_fd)

    def test_lost_status_reader_does_not_terminate_guard_path(self):
        read_fd, write_fd = os.pipe()
        os.close(read_fd)
        guard.report_status(write_fd, 0)

    def test_no_job_on_eof_and_placement_before_exec(self):
        helper = str(Path(guard.__file__).resolve().parent)
        for release in (False, True, "garbled"):
            with self.subTest(release=release), tempfile.TemporaryDirectory() as temp:
                read_fd, write_fd = os.pipe()
                marker, placed = Path(temp) / "ran", Path(temp) / "placed"
                script = ("import sys,os; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
                          "import guard; p=Path(sys.argv[3]); "
                          "s=guard.launch_job([sys.executable,'-I','-c',"
                          "\"import sys; from pathlib import Path; assert Path(sys.argv[1]).exists(); "
                          "Path(sys.argv[2]).touch()\",str(p),sys.argv[4]],int(sys.argv[2]),"
                          "lambda pid:p.write_text(str(pid))); "
                          "sys.exit(125 if s is None else os.waitstatus_to_exitcode(s))")
                child = subprocess.Popen([sys.executable, "-I", "-c", script, helper, str(read_fd),
                                          str(placed), str(marker)], pass_fds=(read_fd,))
                os.close(read_fd)
                try:
                    # Ordering is proved inside argv by requiring the placement
                    # marker, not by an immediate parent-side scheduling check.
                    if release:
                        os.write(write_fd, b"x" if release is True else b"?")
                finally:
                    os.close(write_fd)
                self.assertEqual(0 if release is True else 125, child.wait(timeout=5))
                self.assertEqual(release is True, marker.exists())
                self.assertTrue(placed.exists())

    def test_failed_placement_never_executes_and_reaps_child(self):
        helper = str(Path(guard.__file__).resolve().parent)
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "must-not-run"
            script = '''
import os,sys
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import guard
r,w=os.pipe();os.write(w,b'x');os.close(w)
def refuse(pid):
    raise OSError('simulated placement failure')
try:
    guard.launch_job([sys.executable,'-I','-c',"from pathlib import Path; Path(__import__('sys').argv[1]).touch()",sys.argv[2]],r,refuse)
except OSError:
    pass
else:
    raise RuntimeError('placement failure was hidden')
try:
    os.waitpid(-1,os.WNOHANG)
except ChildProcessError:
    pass
else:
    raise RuntimeError('unreaped child remains')
'''
            result = subprocess.run([sys.executable, "-I", "-c", script, helper, str(marker)],
                                    capture_output=True, timeout=5)
            self.assertEqual(0, result.returncode, result.stderr.decode())
            self.assertFalse(marker.exists())

    def test_status_writer_is_not_inherited_by_executed_job(self):
        helper = str(Path(guard.__file__).resolve().parent)
        script = '''
import os,sys
sys.path.insert(0,sys.argv[1])
import guard
r,w=os.pipe();os.write(w,b'x');os.close(w)
status_r,status_w=os.pipe()
os.set_inheritable(status_w,True)
identity=os.fstat(status_w)
job="""
import os,sys
try:
    stat=os.fstat(int(sys.argv[1]))
except OSError:
    pass
else:
    assert (stat.st_dev,stat.st_ino)!=(int(sys.argv[2]),int(sys.argv[3]))
for name in ('POSTMQ_CREDENTIAL','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR',
             'DBUS_SYSTEM_BUS_ADDRESS','SSH_AUTH_SOCK','NOTIFY_SOCKET'):
    assert name not in os.environ
"""
for name in ('POSTMQ_CREDENTIAL','DBUS_SESSION_BUS_ADDRESS','XDG_RUNTIME_DIR',
             'DBUS_SYSTEM_BUS_ADDRESS','SSH_AUTH_SOCK','NOTIFY_SOCKET'):
    os.environ[name]='test-only-sentinel'
status=guard.launch_job([sys.executable,'-I','-c',job,str(status_w),str(identity.st_dev),str(identity.st_ino)],r,lambda pid:None,close_in_child=(status_w,) if sys.argv[2]=='close' else ())
os.close(status_r);os.close(status_w)
sys.exit(os.waitstatus_to_exitcode(status))
'''
        for close in (True, False):
            result = subprocess.run([sys.executable, "-I", "-c", script, helper,
                                     "close" if close else "leave-open"], capture_output=True, timeout=5)
            if close:
                self.assertEqual(0, result.returncode, result.stderr.decode())
            else:
                self.assertNotEqual(0, result.returncode, "negative control must detect inherited status writer")

    def test_pipe_validation_rejects_direction_alias_and_regular_files(self):
        r1,w1=os.pipe();r2,w2=os.pipe()
        try:
            guard.validate_pipes(r1,w2)
            for first,second in [(w1,w2),(r1,r2)]:
                with self.assertRaises(ValueError):
                    guard.validate_pipes(first,second)
            # Linux identifies both ends of one pipe by the same inode; macOS
            # assigns per-end identifiers. Exercise the Linux identity rule
            # explicitly, without claiming this Mac is a Linux kernel fixture.
            identity=os.fstat(r1)
            with mock.patch.object(guard.os,"fstat",return_value=identity):
                with self.assertRaises(ValueError):guard.validate_pipes(r1,w1)
            with tempfile.TemporaryFile() as file:
                with self.assertRaises(ValueError):
                    guard.validate_pipes(file.fileno(),w2)
            os.set_blocking(r1,False)
            with self.assertRaises(ValueError):guard.validate_pipes(r1,w2)
            os.set_blocking(r1,True)
            os.set_blocking(w2,False)
            with self.assertRaises(ValueError):guard.validate_pipes(r1,w2)
        finally:
            for fd in (r1,w1,r2,w2):os.close(fd)
        with self.assertRaises(OSError):
            guard.validate_pipes(r1,w2)

    def test_publication_pipe_is_separate_directed_and_blocking(self):
        r1,w1=os.pipe();r2,w2=os.pipe();r3,w3=os.pipe()
        try:
            guard.validate_pipes(r1,w2,r3)
            for bad in (r1,w2,w3):
                with self.assertRaises(ValueError):guard.validate_pipes(r1,w2,bad)
            os.set_blocking(r3,False)
            with self.assertRaises(ValueError):guard.validate_pipes(r1,w2,r3)
        finally:
            for fd in (r1,w1,r2,w2,r3,w3):os.close(fd)

    def test_status_error_after_launch_still_holds_evidence(self):
        r1,w1=os.pipe();r2,w2=os.pipe();r3,w3=os.pipe();os.write(w3,b"p");os.close(w3)
        calls=[]
        try:
            with mock.patch.object(guard,"subreaper",side_effect=lambda:calls.append("subreaper")), \
                 mock.patch.object(guard,"current_scope",return_value=Path("/fake")), \
                 mock.patch.object(guard,"create_leaves",return_value=(Path("/fake/guard"),Path("/fake/job"))), \
                 mock.patch.object(guard,"launch_job",side_effect=lambda *a,**k:calls.append("launch") or 0), \
                 mock.patch.object(guard.os,"read",side_effect=AssertionError("must not read publication after a real run")), \
                 mock.patch.object(guard,"report_status",side_effect=OSError("failed pipe")), \
                 mock.patch.object(guard,"hold_evidence",side_effect=lambda:calls.append("hold")):
                guard.main([str(r1),str(w2),"--claim",CLAIM,"--fence","7","--publish-fd",str(r3),"--","app"])
            self.assertEqual(["subreaper","launch","hold"],calls)
        finally:
            for fd in (r1,w1,r2,w2):os.close(fd)

    def test_wait_reaps_adopted_children_before_primary(self):
        with mock.patch.object(guard.os,"waitpid",side_effect=[(99,0),(42,7<<8)]) as wait:
            self.assertEqual(7<<8,guard._wait(42))
            self.assertEqual([mock.call(-1,0),mock.call(-1,0)],wait.call_args_list)

    def test_controls_refuse_symlinks_and_missing_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);target=root/"target";target.write_text("old")
            (root/"link").symlink_to(target)
            for path in [root/"link",root/"missing"]:
                with self.assertRaises(OSError):guard.write_control(path,1)
            self.assertEqual("old",target.read_text())

    def test_scope_lookup_requires_expected_claim_and_real_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();proc=root/"proc";cg=root/"cg"
            (proc/"self").mkdir(parents=True);cg.mkdir()
            unit="postmq-claim-"+CLAIM+"-f7.scope";scope=cg/unit;scope.mkdir()
            record=proc/"self/cgroup";record.write_text("0::/"+unit+"\n")
            self.assertEqual(scope,guard.current_scope(CLAIM,7,proc,cg))
            for text in ["", "0::/"+unit+"\n0::/"+unit, "0::/../"+unit,
                         "0::/postmq-claim-"+CLAIM.lower()+".scope", "0::/postmq-claim-01AAAAAAAAAAAAAAAAAAAAAA02.scope"]:
                record.write_text(text)
                with self.assertRaises(RuntimeError):guard.current_scope(CLAIM,7,proc,cg)
            record.write_text("0::/"+unit)
            scope.rmdir()
            with self.assertRaises(RuntimeError):guard.current_scope(CLAIM,7,proc,cg)
            scope.symlink_to(root,target_is_directory=True)
            with self.assertRaises(RuntimeError):guard.current_scope(CLAIM,7,proc,cg)

    def test_memory_limit_validation_precedes_scope_changes(self):
        for value in [0,-1,True,1]:
            with tempfile.TemporaryDirectory() as temp:
                with self.assertRaises(ValueError):guard.create_leaves(Path(temp),value,writer=lambda *_:None)
                self.assertEqual([],list(Path(temp).iterdir()))

    def test_no_memory_limit_does_not_write_a_memory_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            writes=[]
            guard.create_leaves(Path(temp),None,writer=lambda p,v:writes.append(p.name))
            self.assertNotIn("memory.max",writes)

    def test_abandoned_main_closes_status_and_retains_evidence(self):
        r1,w1=os.pipe();r2,w2=os.pipe();r3,w3=os.pipe();os.write(w3,b"p");os.close(w3)
        try:
            with mock.patch.object(guard,"subreaper"), \
                 mock.patch.object(guard,"current_scope",return_value=Path("/fake")), \
                 mock.patch.object(guard,"create_leaves",return_value=(Path("/fake/guard"),Path("/fake/job"))), \
                 mock.patch.object(guard,"launch_job",return_value=None) as launch, \
                 mock.patch.object(guard,"hold_unexecuted",side_effect=lambda fd,data,published:(os.write(fd,data),os.close(fd))) as hold:
                self.assertEqual(125,guard.main([str(r1),str(w2),"--claim",CLAIM,"--fence","7","--publish-fd",str(r3),"--","app"]))
            self.assertEqual((w2,r3),launch.call_args.kwargs["close_in_child"])
            hold.assert_called_once_with(w2,b"aborted\n",True)
            os.set_blocking(r2,False)  # An unclosed writer must fail, never hang.
            self.assertEqual(b"aborted\n",os.read(r2,100))
        finally:
            for fd in (r1,w1,r2):os.close(fd)

    def test_idle_hold_reaps_available_orphans_before_sleep(self):
        class StopLoop(Exception):pass
        for outcomes in [[(88,0),(99,0),(0,0)],[(88,0),ChildProcessError()]]:
            with mock.patch.object(guard.os,"waitpid",side_effect=outcomes) as wait, \
                 mock.patch.object(guard.time,"sleep",side_effect=StopLoop):
                with self.assertRaises(StopLoop):guard.hold_evidence()
            self.assertEqual([mock.call(-1,os.WNOHANG)]*len(outcomes),wait.call_args_list)

    def test_guard_protection_normalizes_child_reaping(self):
        libc=mock.Mock();libc.prctl.return_value=0
        with mock.patch.object(guard.sys,"platform","linux"), \
             mock.patch.object(guard.ctypes,"CDLL",return_value=libc), \
             mock.patch.object(guard.signal,"signal") as signals:
            guard.subreaper()
        self.assertEqual([mock.call(4,0,0,0,0),mock.call(36,1,0,0,0)],libc.prctl.call_args_list)
        signals.assert_called_once_with(guard.signal.SIGCHLD,guard.signal.SIG_DFL)

    def test_nonterminal_status_closes_writer_and_refuses_success(self):
        read_fd,write_fd=os.pipe()
        try:
            with self.assertRaises(RuntimeError):guard.report_status(write_fd,0x7f)
            self.assertEqual(b"",os.read(read_fd,100))
        finally:
            os.close(read_fd)

    def test_job_receives_default_native_sigpipe(self):
        helper=str(Path(guard.__file__).resolve().parent)
        script='''
import os,sys,signal
sys.path.insert(0,sys.argv[1])
import guard
r,w=os.pipe();os.write(w,b'x');os.close(w)
status=guard.launch_job(['/bin/sh','-c','kill -PIPE $$; exit 99'],r,lambda pid:None)
sys.exit(0 if os.WIFSIGNALED(status) and os.WTERMSIG(status)==signal.SIGPIPE else 1)
'''
        result=subprocess.run([sys.executable,"-I","-c",script,helper],capture_output=True,timeout=5)
        self.assertEqual(0,result.returncode,result.stderr.decode())


if __name__ == "__main__":
    unittest.main()
