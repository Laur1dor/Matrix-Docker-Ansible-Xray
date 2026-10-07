import importlib.machinery
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


def load():
    loader = importlib.machinery.SourceFileLoader('xray_manage', str(Path(__file__).parents[1] / 'xray-manage'))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.swap = self.directory / 'swap'
        self.swap.mkdir()
        (self.directory / 'config.json').write_bytes(b'original config')
        (self.swap / 'links.txt').write_bytes(b'original links')
        (self.swap / 'sub.url').write_bytes(b'https://example.com/sub')
        self.paths = [self.directory / 'config.json', self.swap / 'links.txt', self.swap / 'sub.url']
        self.before = [p.read_bytes() for p in self.paths]

    def execute(self):
        self.m.transaction('refresh', timeout=20, directory=self.directory,
                           backup_root=self.directory / 'backups')

    def assert_preserved(self):
        self.assertEqual([p.read_bytes() for p in self.paths], self.before)

    def test_failed_fetch_preserves_all_live_files(self):
        with patch.object(self.m, 'run', side_effect=RuntimeError('fetch failed')):
            with self.assertRaises(RuntimeError): self.execute()
        self.assert_preserved()

    def test_invalid_subscription_preserves_all_live_files(self):
        with patch.object(self.m, 'run', return_value=b'<html>Forbidden</html>'):
            with self.assertRaises(RuntimeError): self.execute()
        self.assert_preserved()

    def test_failed_validation_does_not_restart_or_replace(self):
        config = json.dumps({'outbounds': [{'tag': 'proxy-0'}]}).encode()
        with patch.object(self.m, 'run', side_effect=[b'vless://id@host:443', config, RuntimeError('invalid')]) as run:
            with self.assertRaises(RuntimeError): self.execute()
        self.assert_preserved()
        self.assertFalse(any(c.args[0][0] == 'systemctl' for c in run.call_args_list))

    def test_failed_probe_rolls_back_and_restarts_previous_config(self):
        config = json.dumps({'outbounds': [{'tag': 'proxy-0'}]}).encode()
        with patch.object(self.m, 'run', side_effect=[b'vless://id@host:443', config, b'', b'', b'']) as run:
            with patch.object(self.m, 'probe', side_effect=RuntimeError('blocked')):
                with patch.object(self.m.time, 'sleep', side_effect=RuntimeError('deadline')):
                    with self.assertRaisesRegex(RuntimeError, 'previous files restored'): self.execute()
        self.assert_preserved()
        self.assertEqual(sum(c.args[0][0] == 'systemctl' for c in run.call_args_list), 2)

    def test_probe_uses_socks_and_rejects_geo_block(self):
        with patch.object(self.m, 'run', return_value=b'403') as run:
            with self.assertRaises(RuntimeError): self.m.probe(self.m.time.monotonic() + 5)
        self.assertIn('socks5h://127.0.0.1:10808', run.call_args.args[0])

    def test_restart_failure_rolls_back_before_probe(self):
        config = json.dumps({'outbounds': [{'tag': 'proxy-0'}]}).encode()
        with patch.object(self.m, 'run', side_effect=[b'vless://id@host:443', config, b'', RuntimeError('restart failed'), b'']):
            with patch.object(self.m, 'probe') as probe:
                with self.assertRaisesRegex(RuntimeError, 'previous files restored'): self.execute()
        self.assert_preserved()
        probe.assert_not_called()


if __name__ == '__main__': unittest.main()
