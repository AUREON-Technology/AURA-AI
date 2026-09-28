/// UX_BASELINE_AUDIT.md F-07: the desktop had Quick Open (files only) and nine
/// keyboard shortcuts with no discoverable list. These cover the palette that
/// replaced that — both its matching/selection rules as plain functions, and
/// the keyboard path a user who never touches the mouse depends on.
library;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:aura_client/core/design_system/aura_theme_v2.dart';
import 'package:aura_client/core/design_system/components/aura_command_palette.dart';

AuraCommandItem _item(
  String title, {
  String group = 'View',
  VoidCallback? onInvoke,
  String? disabledReason,
  String? trailing,
}) {
  return AuraCommandItem(
    id: title,
    title: title,
    group: group,
    icon: Icons.circle_outlined,
    trailing: trailing,
    disabledReason: disabledReason,
    onInvoke: onInvoke ?? (disabledReason == null ? () {} : null),
  );
}

/// The nearest `Semantics` *ancestor* of a row's text is the one InkWell's
/// GestureDetector puts there, which carries none of our properties. Pick the
/// row's own by the property only it sets.
Semantics _rowSemantics(WidgetTester tester, String title) {
  return tester
      .widgetList<Semantics>(
        find.ancestor(of: find.text(title), matching: find.byType(Semantics)),
      )
      .firstWhere((s) => s.properties.selected != null);
}

void main() {
  group('matching', () {
    final toggleSidebar = _item('Toggle Side Bar');

    test('matches on title and on group', () {
      expect(commandMatches(toggleSidebar, 'side'), isTrue);
      expect(commandMatches(toggleSidebar, 'view'), isTrue);
      expect(commandMatches(toggleSidebar, 'SIDE'), isTrue);
    });

    test('every token must match, so extra words narrow rather than widen', () {
      expect(commandMatches(toggleSidebar, 'toggle bar'), isTrue);
      expect(commandMatches(toggleSidebar, 'toggle terminal'), isFalse);
    });

    test('is not fuzzy — an initialism does not match', () {
      // Deliberate: subsequence matching would make "tsb" find this, and also
      // make half the palette match almost anything.
      expect(commandMatches(toggleSidebar, 'tsb'), isFalse);
    });

    test('an empty query returns everything, in registration order', () {
      final items = [_item('A'), _item('B'), _item('C')];
      expect(filterCommands(items, '').map((i) => i.title), ['A', 'B', 'C']);
      expect(filterCommands(items, '   ').map((i) => i.title), ['A', 'B', 'C']);
    });

    test('filtering preserves order rather than ranking', () {
      final items = [_item('Show Panel'), _item('Hide Panel'), _item('Show Explorer')];
      expect(filterCommands(items, 'show').map((i) => i.title), ['Show Panel', 'Show Explorer']);
    });
  });

  group('selection', () {
    test('skips disabled entries when moving', () {
      final items = [
        _item('A'),
        _item('B', disabledReason: 'nope'),
        _item('C'),
      ];
      expect(nextSelectableIndex(items, 0, 1), 2);
      expect(nextSelectableIndex(items, 2, -1), 0);
    });

    test('stops at the ends rather than wrapping', () {
      final items = [_item('A'), _item('B')];
      expect(nextSelectableIndex(items, 1, 1), 1);
      expect(nextSelectableIndex(items, 0, -1), 0);
    });

    test('holding past a trailing run of disabled entries is a no-op', () {
      final items = [
        _item('A'),
        _item('B', disabledReason: 'nope'),
        _item('C', disabledReason: 'nope'),
      ];
      expect(nextSelectableIndex(items, 0, 1), 0);
    });

    test('first selectable skips a leading disabled run, or reports none', () {
      expect(
        firstSelectableIndex([_item('A', disabledReason: 'x'), _item('B')]),
        1,
      );
      expect(firstSelectableIndex([_item('A', disabledReason: 'x')]), -1);
      expect(firstSelectableIndex(const <AuraCommandItem>[]), -1);
    });
  });

  group('the palette itself', () {
    Future<void> open(WidgetTester tester, List<AuraCommandItem> commands) async {
      await tester.pumpWidget(
        MaterialApp(
          theme: AuraThemeV2.dark,
          home: Scaffold(
            body: Builder(
              builder: (context) => ElevatedButton(
                onPressed: () => showAuraCommandPalette(context, commands: commands),
                child: const Text('open'),
              ),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
    }

    testWidgets('lists commands under their group headings', (tester) async {
      await open(tester, [
        _item('Save File', group: 'File'),
        _item('Show Panel', group: 'View'),
      ]);

      expect(find.text('FILE'), findsOneWidget);
      expect(find.text('VIEW'), findsOneWidget);
      expect(find.text('Save File'), findsOneWidget);
    });

    testWidgets('shows the shortcut for a command that has one', (tester) async {
      // The palette is where somebody learns the shortcut for the thing they
      // just did the slow way.
      await open(tester, [_item('Save File', group: 'File', trailing: 'Ctrl+S')]);
      expect(find.text('Ctrl+S'), findsOneWidget);
    });

    testWidgets('typing narrows the list', (tester) async {
      await open(tester, [
        _item('Save File', group: 'File'),
        _item('Show Panel', group: 'View'),
      ]);

      await tester.enterText(find.byType(TextField), 'panel');
      await tester.pumpAndSettle();

      expect(find.text('Show Panel'), findsOneWidget);
      expect(find.text('Save File'), findsNothing);
    });

    testWidgets('a query matching nothing explains itself and offers a way out',
        (tester) async {
      await open(tester, [_item('Save File', group: 'File')]);

      await tester.enterText(find.byType(TextField), 'zzzz');
      await tester.pumpAndSettle();

      expect(find.text('No matching commands'), findsOneWidget);
      // Not a dead end: the empty state can clear the search itself.
      await tester.tap(find.text('Clear search'));
      await tester.pumpAndSettle();
      expect(find.text('Save File'), findsOneWidget);
    });

    testWidgets('an unavailable command is listed with its reason, not hidden',
        (tester) async {
      await open(tester, [
        _item('Go to File...', group: 'File', disabledReason: 'No workspace is open'),
      ]);

      expect(find.text('Go to File...'), findsOneWidget);
      expect(find.text('No workspace is open'), findsOneWidget);

      expect(_rowSemantics(tester, 'Go to File...').properties.enabled, isFalse);
    });

    testWidgets('Enter runs the selected command and closes the palette', (tester) async {
      var invoked = 0;
      await open(tester, [_item('Show Panel', onInvoke: () => invoked++)]);

      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();

      expect(invoked, 1);
      expect(find.byType(AuraCommandPalette), findsNothing);
    });

    testWidgets('arrow keys move the selection past disabled entries', (tester) async {
      var ran = '';
      await open(tester, [
        _item('First', onInvoke: () => ran = 'First'),
        _item('Blocked', disabledReason: 'nope'),
        _item('Third', onInvoke: () => ran = 'Third'),
      ]);

      // Down once from 'First' lands on 'Third', not on 'Blocked'.
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
      await tester.pump();
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();

      expect(ran, 'Third');
    });

    testWidgets('arrow keys drive the list, not the caret in the search field',
        (tester) async {
      var ran = '';
      await open(tester, [
        _item('First', onInvoke: () => ran = 'First'),
        _item('Second', onInvoke: () => ran = 'Second'),
      ]);

      await tester.enterText(find.byType(TextField), 'e');
      await tester.pumpAndSettle();
      await tester.sendKeyEvent(LogicalKeyboardKey.arrowDown);
      await tester.pump();
      await tester.sendKeyEvent(LogicalKeyboardKey.enter);
      await tester.pumpAndSettle();

      expect(ran, 'Second');
    });

    testWidgets('Escape closes it without running anything', (tester) async {
      var invoked = 0;
      await open(tester, [_item('Show Panel', onInvoke: () => invoked++)]);

      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pumpAndSettle();

      expect(find.byType(AuraCommandPalette), findsNothing);
      expect(invoked, 0);
    });

    testWidgets('the selected row is exposed to semantics, not only tinted',
        (tester) async {
      await open(tester, [_item('First'), _item('Second')]);

      expect(_rowSemantics(tester, 'First').properties.selected, isTrue);
      expect(_rowSemantics(tester, 'Second').properties.selected, isFalse);
    });
  });
}
