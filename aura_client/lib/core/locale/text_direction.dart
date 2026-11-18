import 'package:flutter/widgets.dart';

/// Text direction for content whose language is not the UI's
/// (docs/plans/arabic-language_plan.md, stage 2).
///
/// Two rules:
/// * code, terminal output, file paths, diffs and logs are always left-to-right, whatever the
///   app language: mirroring them makes them unreadable ([LtrContent]);
/// * a chat message, or what the user is typing, takes the direction of its own first strong
///   character, the way HTML's `dir="auto"` does: an Arabic message in an English app reads
///   right-to-left, and English or code in an Arabic app reads left-to-right
///   ([directionOfText]).

/// Arabic-script letters (main block letters, supplement, extended-A, presentation forms) and
/// Hebrew: the right-to-left scripts a message here may realistically start with.
final _rtlStrong = RegExp(
  r'[֐-׿ؠ-يٮ-ۓۺ-ۿݐ-ݿࢠ-ࣿיִ-﷿ﹰ-ﻼ]',
);

/// Latin, Greek, Cyrillic and the other common left-to-right letters.
final _ltrStrong = RegExp(r'[A-Za-zÀ-ɏͰ-ϿЀ-ӿ]');

/// Markdown and code that say nothing about the language a message is written in: fenced and
/// inline code, link targets, and URLs.
final _notProse = RegExp(r'```[\s\S]*?```|`[^`\n]*`|\]\([^)]*\)|https?://\S+');

/// The direction of [text]'s first strong character outside code and URLs, or null when it has
/// none (empty, digits, emoji) and the ambient direction should stand.
TextDirection? directionOfText(String text) {
  final prose = text.replaceAll(_notProse, ' ');
  for (final rune in prose.runes) {
    final char = String.fromCharCode(rune);
    if (_rtlStrong.hasMatch(char)) return TextDirection.rtl;
    if (_ltrStrong.hasMatch(char)) return TextDirection.ltr;
  }
  return null;
}

/// Lays [child] out left-to-right regardless of the app language: code, terminal output,
/// paths, diffs, logs.
class LtrContent extends StatelessWidget {
  const LtrContent({super.key, required this.child});

  final Widget child;

  @override
  Widget build(BuildContext context) =>
      Directionality(textDirection: TextDirection.ltr, child: child);
}
