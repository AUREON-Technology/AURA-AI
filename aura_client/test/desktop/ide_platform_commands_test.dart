// WP7 (D-08b): the IDE pieces that used to assume Windows now follow the platform they run on.
// The Linux terminal is a POSIX shell (aura_hostd's PosixPtySession runs $SHELL or /bin/bash),
// not PowerShell, so its emulator mode and its `cd` quoting differ; Reveal opens the file
// manager with `xdg-open` instead of telling a Linux user it only works on Windows.
import 'package:aura_client/desktop/shell/ide_platform_commands.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:xterm/xterm.dart' as xterm;

void main() {
  group('terminal emulator platform', () {
    test('Windows keeps the Windows (ConPTY) mode', () {
      expect(ideTerminalTargetPlatform(TargetPlatform.windows), xterm.TerminalTargetPlatform.windows);
    });

    test('Linux gets the Linux mode', () {
      expect(ideTerminalTargetPlatform(TargetPlatform.linux), xterm.TerminalTargetPlatform.linux);
    });
  });

  group('"open in terminal" cd line', () {
    test('PowerShell on Windows doubles an embedded single quote', () {
      expect(
        ideTerminalCdCommand(r"C:\Users\o'brien\repo", TargetPlatform.windows),
        "cd 'C:\\Users\\o''brien\\repo'\r",
      );
    });

    test("a POSIX shell on Linux closes, escapes and reopens the quote", () {
      expect(
        ideTerminalCdCommand("/home/o'brien/my repo", TargetPlatform.linux),
        "cd '/home/o'\\''brien/my repo'\r",
      );
    });

    test('nothing in a POSIX path is expanded by the shell', () {
      expect(ideTerminalCdCommand(r'/home/me/$HOME `x`', TargetPlatform.linux), "cd '/home/me/\$HOME `x`'\r");
    });
  });

  group('reveal in file manager', () {
    // A record's list field compares by identity, so compare the parts.
    Matcher runs(String program, List<String> args) => isA<(String, List<String>)>()
        .having((c) => c.$1, 'program', program)
        .having((c) => c.$2, 'arguments', args);

    test('Windows selects a file in Explorer and opens a folder', () {
      expect(ideRevealCommand(r'C:\repo\a.txt', isDirectory: false, platform: TargetPlatform.windows),
          runs('explorer', ['/select,', r'C:\repo\a.txt']));
      expect(ideRevealCommand(r'C:\repo', isDirectory: true, platform: TargetPlatform.windows),
          runs('explorer', [r'C:\repo']));
    });

    test('Linux opens the folder, or a file\'s parent folder, with xdg-open', () {
      expect(ideRevealCommand('/home/me/repo', isDirectory: true, platform: TargetPlatform.linux),
          runs('xdg-open', ['/home/me/repo']));
      expect(ideRevealCommand('/home/me/repo/lib/a.dart', isDirectory: false, platform: TargetPlatform.linux),
          runs('xdg-open', ['/home/me/repo/lib']));
    });

    test('there is nothing to run where the IDE does not run', () {
      expect(ideRevealCommand('/x', isDirectory: true, platform: TargetPlatform.macOS), isNull);
    });
  });
}
