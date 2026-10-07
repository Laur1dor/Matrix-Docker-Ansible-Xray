import importlib.util
from pathlib import Path
import subprocess
import types
import unittest
from unittest.mock import patch


def load():
    spec = importlib.util.spec_from_file_location('check_updates', Path(__file__).parents[1] / 'check-updates.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class UpdateCheckTests(unittest.TestCase):
    def test_fetch_failure_stops_before_up_to_date_or_notification(self):
        checker = load()
        with patch.object(checker.subprocess, 'run', side_effect=[types.SimpleNamespace(returncode=0, stdout='master'), types.SimpleNamespace(returncode=1, stdout='')]) as run:
            with patch.object(checker.urllib.request, 'urlopen') as send:
                with patch('builtins.print') as output:
                    with self.assertRaisesRegex(RuntimeError, 'fetch'): checker.main()
        output.assert_not_called()
        send.assert_not_called()
        self.assertIn('http.proxy=socks5h://127.0.0.1:10808', run.call_args.args[0])
        self.assertEqual(run.call_args.kwargs['timeout'], 40)

    def test_fetch_timeout_is_an_error(self):
        checker = load()
        with patch.object(checker.subprocess, 'run', side_effect=subprocess.TimeoutExpired('git', 40)):
            with self.assertRaisesRegex(RuntimeError, 'timed out'): checker.git('fetch', '-q', 'origin')

    def test_changelog_is_read_from_repo_root_without_sending(self):
        checker = load()
        checker.STATE = '/nonexistent-update-test-state'
        calls = []
        def fake_git(*args):
            calls.append(args)
            if args[0] == 'diff': raise RuntimeError('stop after inspecting diff path')
            return {'fetch': '', 'rev-list': '1', 'log': 'commit', 'rev-parse': 'master' if '--abbrev-ref' in args else ('local' if 'HEAD' in args else 'remote')}[args[0]]
        with patch.object(checker, 'git', side_effect=fake_git):
            with patch.object(checker.urllib.request, 'urlopen') as send:
                with self.assertRaisesRegex(RuntimeError, 'stop after'): checker.main()
        self.assertEqual(calls[-1], ('diff', 'HEAD..origin/master', '--', 'CHANGELOG.md'))
        send.assert_not_called()
