"""Credential-free refusal/identity controls; never launches a managed job."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("mac_checks_entry", Path(__file__).with_name("entry.py"))
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)
p = entry.module("provider"); n = entry.module("native")
HOST = {"host_uuid":"aaaaaaaa-aaaa-4aaa-8aaa-000000000002", "boot_uuid":"aaaaaaaa-aaaa-4aaa-8aaa-000000000004"}
CID = "01AAAAAAAAAAAAAAAAAAAAAA05"


class FakeNative:
    generation = HOST
    free = 90
    def host(self): return dict(self.generation)
    def pressure(self): return {"free_percent":self.free, "total_bytes":64*1024**3}
    def process(self, pid): return None
    def now(self): return 100.0


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pmac-unit-", dir="/tmp")
        self.root = Path(self.temp.name).resolve()/"native"
        self.config = {"expected_host":HOST, "max_run_seconds":10, "minimum_free_percent":30,
                       "owner_mode":"background", "allowed_executables":{"/bin/echo":"0"*64}}
        self.native = FakeNative(); self.provider = p.MacProvider(self.config, native=self.native, state_root=self.root)
    def tearDown(self): self.temp.cleanup()
    def test_logical_generic_contract(self):
        d = self.provider.declare()
        self.assertEqual((d["identity_kind"],d["receipt_provider"]), ("logical","generic"))
        self.assertTrue(d["capabilities"]["test_fixture"])
        self.assertEqual(set(d["enforcement"].values()), {"advisory"})
    def test_host_generation_refuses(self):
        self.native.generation = {**HOST,"boot_uuid":"aaaaaaaa-aaaa-4aaa-8aaa-000000000005"}
        self.assertEqual(self.provider.preflight({"claim_id":CID,"fence":1,"state":"granted"})["result"],"refused")
    def test_low_headroom_refuses(self):
        self.native.free = 20
        self.assertEqual(self.provider.telemetry()[1], "degraded")
    def test_owner_interactive_refuses(self):
        self.provider.config["owner_mode"] = "interactive"
        self.assertEqual(self.provider.telemetry()[1],"degraded")
    def test_missing_epoch_blocks_new_work(self):
        (self.root/"e"/p.epoch_name(CID,1)).mkdir(mode=0o700)
        self.assertEqual(self.provider.telemetry()[0]["unsettled_epochs"],1)
        self.assertEqual(self.provider.telemetry()[1],"degraded")
    def test_root_replacement_refuses(self):
        self.root.rename(self.root.with_name("old")); self.root.mkdir(mode=0o700)
        self.assertEqual(self.provider.telemetry()[1],"unknown")
    def test_unknown_reference_never_clean(self):
        self.assertEqual(self.provider.inspect("invented")["state"],"unverifiable")
        self.assertEqual(self.provider.release_receipt("invented","completed")["cleanup"],"unknown")
        self.assertEqual(self.provider.reconcile_verdict([],CID,1)[0],"unknown")
    def test_environment_excludes_credentials(self):
        env = p.job_environment({"POSTMQ_API_KEY":"secret","AWS_SECRET_ACCESS_KEY":"secret","PATH":"/usr/bin","PYTHONPATH":"bad"})
        self.assertNotIn("POSTMQ_API_KEY",env); self.assertNotIn("AWS_SECRET_ACCESS_KEY",env)
        self.assertNotIn("PYTHONPATH",env); self.assertEqual(env["PATH"],"/usr/bin")
    def test_fork_or_observation_failure_cannot_be_clean(self):
        status = {"phase":"terminal","kernel_exit_observed":True,"reaped_by_guard":True,
                  "fork_seen":False,"reason":None,"job_exit":0}
        self.assertTrue(p.clean_status(status))
        for field,value in [("fork_seen",True),("kernel_exit_observed",False),("reaped_by_guard",False),
                            ("reason","kernel observation error"),("phase","unverifiable")]:
            self.assertFalse(p.clean_status({**status,field:value}))
    def test_bool_fence_and_invalid_claim_refused(self):
        for cid,fence in [(CID,True),("../bad",1),(CID,0)]:
            with self.assertRaises(n.ProviderError): p.epoch_name(cid,fence)
    def test_private_records_only(self):
        fd = n.private_dir(self.root)
        try:
            n.write_json(fd,"record",{"ok":True})
            self.assertEqual(n.read_json(fd,"record"),{"ok":True})
            os.chmod(self.root/"record",0o644)
            with self.assertRaises(n.ProviderError): n.read_json(fd,"record")
        finally: os.close(fd)
    def test_config_rejects_symlink_and_world_readable(self):
        file = self.root/"config.json"; file.write_text(json.dumps(self.config)); file.chmod(0o600)
        self.assertEqual(p.read_config(file),self.config)
        alias = self.root/"alias"; alias.symlink_to(file)
        with self.assertRaises(n.ProviderError): p.read_config(alias)
        file.chmod(0o644)
        with self.assertRaises(n.ProviderError): p.read_config(file)
    def test_unapproved_executable_leaves_release_fd_open(self):
        r,w = os.pipe()
        try:
            with self.assertRaises(n.ProviderError): self.provider.container(CID,1,["/bin/sh"],{},r)
            os.fstat(r)
            self.assertEqual(list((self.root/"e").iterdir()),[])
        finally: os.close(r); os.close(w)



if __name__ == "__main__": unittest.main(verbosity=2)
