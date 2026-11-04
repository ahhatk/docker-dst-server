import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / 'scripts_system/entrypoint.sh'


class HardeningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / 'mock-bin'
        self.bin.mkdir()
        self.count = self.root / 'count'
        steamcmd = self.bin / 'steamcmd'
        steamcmd.write_text('''#!/bin/sh
count=0
test ! -f "$COUNT" || count=$(cat "$COUNT")
count=$((count + 1))
echo "$count" > "$COUNT"
if [ "$count" -le "$FAILURES" ]; then exit 17; fi
if [ "${CREATE_GAME:-0}" = 1 ]; then mkdir -p "$GAME/bin" "$GAME/bin64"; fi
''')
        steamcmd.chmod(0o755)
        sleep = self.bin / 'sleep'
        sleep.write_text('#!/bin/sh\nexit 0\n')
        sleep.chmod(0o755)
        self.env = dict(os.environ, PATH=f'{self.bin}:{os.environ["PATH"]}',
                        COUNT=str(self.count), FAILURES='0', GAME=str(self.root / 'game'),
                        DST_USER='test', DST_GROUP='test')

    def run_shell(self, script, shell='bash'):
        return subprocess.run([shell, '-c', script], env=self.env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def attempts(self):
        return int(self.count.read_text()) if self.count.exists() else 0

    def build_step(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        step = dockerfile.split('# install Don\'t Starve Together server\n', 1)[1].split('\n\n', 1)[0]
        return (step.removeprefix('RUN ').replace('\\\n', '')
                .replace('/opt/dst_server', str(self.root / 'game'))
                .replace('chown -R "${DST_USER}:${DST_GROUP}"', 'true')
                .replace('rm -rf /root/Steam /root/.steam', 'true'))

    def update_step(self):
        script = ENTRYPOINT.read_text()
        return script.split('    echo "Updating server..."\n', 1)[1].split('\n    # if there are no mods config', 1)[0]

    def token_step(self):
        script = ENTRYPOINT.read_text()
        return (script.split('    # check cluster token file format\n', 1)[1]
                .split('\n    # fix config file permission', 1)[0]
                .replace('/tmp/cluster_token.txt', str(self.root / 'token-tmp')))

    def test_build_retries_then_accepts_game(self):
        self.env.update(DST_DOWNLOAD='1', FAILURES='2', CREATE_GAME='1')
        result = self.run_shell(self.build_step(), 'sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attempts(), 3)

    def test_build_fails_when_install_fails_or_game_missing(self):
        for failures in ('3', '0'):
            with self.subTest(failures=failures):
                self.count.unlink(missing_ok=True)
                self.env.update(DST_DOWNLOAD='1', FAILURES=failures, CREATE_GAME='0')
                result = self.run_shell(self.build_step(), 'sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ERROR: DST server download failed', result.stderr)

    def test_build_skip_does_not_run_steamcmd(self):
        self.env['DST_DOWNLOAD'] = '0'
        result = self.run_shell(self.build_step(), 'sh')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Skipping DST server download', result.stdout)
        self.assertEqual(self.attempts(), 0)

    def test_build_requires_each_binary_directory(self):
        self.env.update(DST_DOWNLOAD='1', CREATE_GAME='0')
        game = self.root / 'game'
        for present in ('bin', 'bin64'):
            with self.subTest(present=present):
                (game / present).mkdir(parents=True, exist_ok=True)
                result = self.run_shell(self.build_step(), 'sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('ERROR: DST server download failed', result.stderr)
                (game / present).rmdir()

    def test_build_rejects_exhaustion_even_if_game_directories_exist(self):
        self.env.update(DST_DOWNLOAD='1', FAILURES='3')
        (self.root / 'game/bin').mkdir(parents=True)
        (self.root / 'game/bin64').mkdir()
        result = self.run_shell(self.build_step(), 'sh')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('ERROR: DST server download failed', result.stderr)
        self.assertEqual(self.attempts(), 3)

    def test_update_retries_then_succeeds(self):
        self.env['FAILURES'] = '2'
        result = self.run_shell('set -Eeuo pipefail\n' + self.update_step())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.attempts(), 3)

    def test_update_exhaustion_fails_and_invokes_error_trap(self):
        self.env['FAILURES'] = '3'
        handler = ENTRYPOINT.read_text().split('# set -e error handler.\n', 1)[1].split('\nif [ "$1"', 1)[0]
        result = self.run_shell('set -Eeuo pipefail\n' + handler + '\n' + self.update_step())
        self.assertEqual(result.returncode, 17)
        self.assertIn('RET 17', result.stderr)
        self.assertEqual(self.attempts(), 3)

    def test_missing_or_empty_token_fails(self):
        token = self.root / 'token'
        self.env['FILE_CLUSTER_TOKEN'] = str(token)
        for exists in (False, True):
            with self.subTest(exists=exists):
                if exists:
                    token.touch()
                result = self.run_shell(self.token_step())
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Please fill in', result.stderr)

    def test_nonempty_token_newline_is_normalized(self):
        token = self.root / 'token'
        token.write_text('example-token\n')
        self.env['FILE_CLUSTER_TOKEN'] = str(token)
        result = self.run_shell(self.token_step())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(token.read_text(), 'example-token')


if __name__ == '__main__':
    unittest.main()
