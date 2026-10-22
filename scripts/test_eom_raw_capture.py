"""Focused standalone storage/interruption regression; never accesses hardware."""
import contextlib
import copy
import csv
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import numpy as np

spec=importlib.util.spec_from_file_location('eom_raw_capture',Path(__file__).with_name('eom_raw_capture.py'))
raw=importlib.util.module_from_spec(spec);spec.loader.exec_module(raw)


class RawCaptureTest(unittest.TestCase):
    def test_storage_interruption_and_append(self):
        with tempfile.TemporaryDirectory(prefix='eom-raw-test-') as temporary, contextlib.redirect_stdout(io.StringIO()):
            c=json.loads(Path(__file__).with_name('eom_raw_capture.json').read_text())
            c['output_directory']=temporary
            c['dc'].update(points=5,decimation=1)
            c['dynamic'].update(log_points=2,frequency_low_hz=10.,frequency_high_hz=100.,extra_frequencies_hz=[],passes=['ascending'])
            directory,status,archive=raw.acquire(c,'dc',mock=True)
            self.assertEqual(status['state'],'COMPLETED');self.assertTrue(archive['zip_verified'])
            self.assertEqual(archive['captures'],20)
            with (directory/'index.csv').open() as source: rows=list(csv.DictReader(source))
            self.assertTrue(any((np.load(directory/r['raw_file'])<0).any() for r in rows))
            for r in rows:
                self.assertEqual(np.load(directory/r['raw_file']).shape,(2,raw.N))
                self.assertEqual(raw.sha(directory/r['raw_file']),r['sha256'])
            raw.acquire(c,'dynamic',mock=True,append=directory)
            self.assertEqual(raw.verify_and_zip(directory)['captures'],24)
            with zipfile.ZipFile(archive['zip']) as z: self.assertIsNone(z.testzip())
            interrupted,state,partial=raw.acquire(c,'dc',mock=True,interrupt_after=3)
            self.assertEqual(state['state'],'INTERRUPTED');self.assertEqual(state['cleanup']['status'],'COMPLETED')
            self.assertEqual(partial['captures'],3);self.assertTrue(partial['zip_verified'])
            self.assertIn('KeyboardInterrupt',(interrupted/'events.jsonl').read_text())
            broken=copy.deepcopy(c);broken['confirmed']={}
            with self.assertRaises(ValueError): raw.plan(broken,'dc')
            broken=copy.deepcopy(c);broken['chain']['preamp_gain']=float('nan')
            with self.assertRaises(ValueError): raw.plan(broken,'dc')
            broken=copy.deepcopy(c);broken['dc']['high_v']=.9
            with self.assertRaises(ValueError): raw.plan(broken,'dc')

    def test_clipping_keeps_failed_buffer_and_cleanup(self):
        class Clipped(raw.MockBench):
            def capture(self):
                samples,meta=super().capture();samples[0,10]=8191
                return samples,meta
        with tempfile.TemporaryDirectory(prefix='eom-raw-fault-') as temporary, contextlib.redirect_stdout(io.StringIO()):
            c=json.loads(Path(__file__).with_name('eom_raw_capture.json').read_text());c['output_directory']=temporary
            with patch.object(raw,'MockBench',Clipped): directory,state,archive=raw.acquire(c,'dc',mock=True)
            self.assertEqual(state['state'],'FAILED');self.assertEqual(state['cleanup']['status'],'COMPLETED')
            self.assertEqual(archive['captures'],1);self.assertEqual(archive['accepted'],0);self.assertTrue(archive['zip_verified'])
            self.assertEqual(np.load(directory/'captures/00000.npy')[0,10],8191)


if __name__=='__main__': unittest.main()
