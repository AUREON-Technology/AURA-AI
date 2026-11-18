import 'package:aura_hostd/src/process_env.dart';
import 'package:test/test.dart';

void main() {
  group('minimalEnvironment (G-06, SEC-04)', () {
    const daemon = {
      'PATH': '/usr/bin:/bin',
      'HOME': '/home/u',
      'USER': 'u',
      'LOGNAME': 'u',
      'LANG': 'en_US.UTF-8',
      'LC_ALL': 'C.UTF-8',
      'LC_CTYPE': 'C.UTF-8',
      'TERM': 'xterm',
      'TMPDIR': '/tmp/u',
      'SHELL': '/bin/bash',
      'GITHUB_TOKEN': 'ghp_secret',
      'AWS_SECRET_ACCESS_KEY': 'aws_secret',
      'ANTHROPIC_API_KEY': 'sk-secret',
      'SSH_AUTH_SOCK': '/tmp/agent.sock',
      'LD_PRELOAD': '/evil.so',
      'AURA_HOSTD_TOKEN': 'tok',
    };

    test('keeps only the allowlisted variables on POSIX', () {
      final env = minimalEnvironment(daemon, windows: false);
      expect(env.keys.toSet(), {
        'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TERM', 'TMPDIR', 'SHELL',
      });
      expect(env['PATH'], '/usr/bin:/bin');
      expect(env.values, isNot(contains('ghp_secret')));
    });

    test('keeps the Windows system variables, matched case-insensitively, and drops the rest', () {
      final env = minimalEnvironment({
        'Path': r'C:\Windows;C:\Git\cmd',
        'USERPROFILE': r'C:\Users\u',
        'SystemRoot': r'C:\Windows',
        'SystemDrive': 'C:',
        'ComSpec': r'C:\Windows\system32\cmd.exe',
        'PATHEXT': '.COM;.EXE;.BAT;.CMD',
        'windir': r'C:\Windows',
        'APPDATA': r'C:\Users\u\AppData\Roaming',
        'LOCALAPPDATA': r'C:\Users\u\AppData\Local',
        'ProgramData': r'C:\ProgramData',
        'ProgramFiles': r'C:\Program Files',
        'ProgramFiles(x86)': r'C:\Program Files (x86)',
        'ProgramW6432': r'C:\Program Files',
        'TEMP': r'C:\Users\u\AppData\Local\Temp',
        'TMP': r'C:\Users\u\AppData\Local\Temp',
        'GITHUB_TOKEN': 'ghp_secret',
        'OPENAI_API_KEY': 'sk-secret',
      }, windows: true);
      expect(env.keys, containsAll(['Path', 'USERPROFILE', 'SystemRoot', 'ComSpec', 'windir',
          'ProgramFiles(x86)', 'ProgramW6432', 'TEMP', 'TMP']));
      expect(env.containsKey('GITHUB_TOKEN'), isFalse);
      expect(env.containsKey('OPENAI_API_KEY'), isFalse);
    });

    test('does not keep Windows-only names on POSIX', () {
      final env = minimalEnvironment({'APPDATA': 'x', 'ComSpec': 'y', 'PATH': '/bin'}, windows: false);
      expect(env.keys, ['PATH']);
    });
  });
}
