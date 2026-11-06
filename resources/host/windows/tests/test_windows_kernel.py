"""Kernel-epoch parsing faults and actual read-only Windows observation."""
import importlib.util
from pathlib import Path
import struct
import sys
import unittest
import uuid

spec=importlib.util.spec_from_file_location('kernel_epoch_tests',Path(__file__).with_name('windows_kernel.py'))
k=importlib.util.module_from_spec(spec);spec.loader.exec_module(k)
MACHINE='aaaaaaaa-aaaa-4aaa-8aaa-00000000000d'


def records(items):
    raw=bytearray(256*len(items));base=0x100000
    for index,(pid,parent,birth,name) in enumerate(items):
        start=index*256;label=name.encode('utf-16-le')
        struct.pack_into('<II',raw,start,256 if index+1<len(items) else 0,3)
        struct.pack_into('<Q',raw,start+32,birth)
        struct.pack_into('<HH',raw,start+56,len(label),len(label))
        struct.pack_into('<Q',raw,start+64,base+start+120)
        struct.pack_into('<QQ',raw,start+80,pid,parent)
        raw[start+120:start+120+len(label)]=label
    return bytes(raw),base


class Parser(unittest.TestCase):
    def setUp(self):self.raw,self.base=records([(4,0,100,'System'),(123,17,1000,'python.exe')])
    def parse(self,raw=None):return k.parse_kernel_birth(self.raw if raw is None else raw,self.base,123,1000)
    def test_current_process_birth_cross_checks_the_reserved_layout(self):self.assertEqual(self.parse(),100)
    def test_truncation_or_bad_stride_is_refused(self):
        for raw in (self.raw[:103],self.raw[:300],struct.pack('<I',255)+self.raw[4:]):
            with self.subTest(size=len(raw)),self.assertRaises(ValueError):self.parse(raw)
    def test_wrong_caller_birth_or_missing_caller_is_refused(self):
        for items in ([(4,0,100,'System')],[(4,0,100,'System'),(123,17,999,'python.exe')]):
            raw,_=records(items)
            with self.assertRaises(ValueError):self.parse(raw)
    def test_duplicate_caller_or_system_is_refused(self):
        for items in ([(4,0,100,'System'),(123,17,1000,'python.exe'),(123,17,1000,'python.exe')],
                      [(4,0,100,'System'),(4,0,100,'System'),(123,17,1000,'python.exe')]):
            raw,_=records(items)
            with self.assertRaises(ValueError):self.parse(raw)
    def test_kernel_name_parent_and_time_must_match(self):
        for system in ((4,1,100,'System'),(4,0,100,'Other!'),(4,0,0,'System'),(4,0,1000,'System')):
            raw,_=records([system,(123,17,1000,'python.exe')])
            with self.subTest(system=system),self.assertRaises(ValueError):self.parse(raw)
    def test_system_name_pointer_cannot_escape_its_record(self):
        raw=bytearray(self.raw);struct.pack_into('<Q',raw,64,self.base+len(raw)-4)
        with self.assertRaises(ValueError):self.parse(bytes(raw))
        struct.pack_into('<Q',raw,64,self.base-1)
        with self.assertRaises(ValueError):self.parse(bytes(raw))


class Identifier(unittest.TestCase):
    def test_same_kernel_is_stable_and_different_kernel_or_machine_changes(self):
        first=k.identifier(MACHINE,'100')
        expected=str(uuid.uuid5(uuid.uuid5(uuid.NAMESPACE_OID,'postmq.windows-kernel-process-epoch-v2'),MACHINE+'|100'))
        self.assertEqual(first,expected)
        self.assertEqual(uuid.UUID(first).version,5)
        self.assertEqual(first,k.identifier(MACHINE,'100'))
        self.assertNotEqual(first,k.identifier(MACHINE,'101'))
        self.assertNotEqual(first,k.identifier('aaaaaaaa-aaaa-4aaa-8aaa-000000000002','100'))
    def test_only_exact_canonical_machine_and_decimal_birth_are_accepted(self):
        for birth in (0,100,'0','01','-1','1.0',str(2**64)):
            with self.subTest(birth=birth),self.assertRaises(ValueError):k.identifier(MACHINE,birth)
        for machine in ('other',MACHINE.upper(),'00000000-0000-0000-0000-000000000000'):
            with self.assertRaises(ValueError):k.identifier(machine,'100')


@unittest.skipUnless(sys.platform=='win32','actual read-only Windows x64 observation')
class Native(unittest.TestCase):
    def test_two_current_kernel_observations_are_stable(self):
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\Microsoft\Cryptography',0,
                           winreg.KEY_READ|winreg.KEY_WOW64_64KEY) as key:
            machine=winreg.QueryValueEx(key,'MachineGuid')[0].lower()
        first=k.snapshot(machine);second=k.snapshot(machine)
        self.assertEqual(first,second)
        self.assertEqual(first['boot_identifier'],k.identifier(machine,first['kernel_system_creation_windows_100ns']))
        self.assertTrue(first['kernel_system_creation_windows_100ns'].isdigit())


if __name__=='__main__':unittest.main()
