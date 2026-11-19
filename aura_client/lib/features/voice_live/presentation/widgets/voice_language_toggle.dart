import 'package:flutter/material.dart';

import 'package:aura_client/core/ai/voice/voice_language.dart';
import 'package:aura_client/core/design_system/design_system.dart';
import 'package:aura_client/l10n/l10n.dart';

/// English / العربية switch for the language the user speaks in (Phase V).
///
/// Each label is laid out in its own script direction, so the Arabic option reads correctly
/// inside an English (LTR) screen.
class VoiceLanguageToggle extends StatelessWidget {
  const VoiceLanguageToggle({
    super.key,
    required this.value,
    required this.onChanged,
  });

  final VoiceLanguage value;
  final ValueChanged<VoiceLanguage> onChanged;

  @override
  Widget build(BuildContext context) {
    final palette = AuraPalette.of(context);
    final l10n = context.l10n;
    return Semantics(
      label: l10n.voiceLanguageLabel,
      container: true,
      child: SegmentedButton<VoiceLanguage>(
        showSelectedIcon: false,
        style: SegmentedButton.styleFrom(
          foregroundColor: palette.textSecondary,
          selectedForegroundColor: palette.textPrimary,
          selectedBackgroundColor: palette.surfaceVariant,
          side: BorderSide(color: palette.border),
          visualDensity: VisualDensity.compact,
        ),
        segments: [
          ButtonSegment(
            value: VoiceLanguage.english,
            label: Text(VoiceLanguage.english.nativeName, textDirection: TextDirection.ltr),
          ),
          ButtonSegment(
            value: VoiceLanguage.arabic,
            label: Text(VoiceLanguage.arabic.nativeName, textDirection: TextDirection.rtl),
          ),
        ],
        selected: {value},
        onSelectionChanged: (selection) => onChanged(selection.single),
      ),
    );
  }
}
