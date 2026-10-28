# Component Guide

What exists in `aura_client/lib/core/design_system/components/`, what each one is for, and what
is still a per-screen one-off. Import the barrel —
`package:aura_client/core/design_system/design_system.dart` — rather than individual files.

**The rule:** before building a widget, look here. If something close exists, extend it; if
nothing does, add it here rather than in the feature folder. A component that lives in one
screen's folder gets copied into the next screen rather than reused.

---

## The set today

| Component | For | Notes |
|---|---|---|
| `AuraButton` | All four button variants | `primary` / `secondary` / `ghost` / `destructive`, plus `loading` and `expand`. Enforces the accessible minimum tap target so call sites can't forget it. |
| `AuraCard` | A grouped surface | |
| `AuraCommandPalette` | Searchable command list | Takes a plain `List<AuraCommandItem>`; knows nothing about the desktop or about routing. See below. |
| `AuraDialog` | Modal with title + actions | `AuraDialog.show()` is the entry point. |
| `AuraEmptyState` | Empty, error and offline states | Icon, title, message, up to two actions, and an `AuraStateTone`. The tone is announced as well as painted, so an error is distinguishable from an empty list without seeing colour. An error state with no `actionLabel` is a dead end — give it one. |
| `AuraListTile` | Row in a list | |
| `AuraSectionHeader` | Heading above a group | |
| `AuraSelectTile` | Row that picks one of several | |
| `AuraSkeleton` | Loading placeholder | Prefer over a bare spinner where the shape of the content is known. |
| `AuraSnackbar` | Transient confirmation | |
| `AuraSyncStatusView` | "Is my work saved?" | In `core/sync/`, not the UI kit — the design system has no business knowing what a parked outbox row is. Folds `SyncState` and the parked count into five phases; see `TRANSFORMATION_STATUS.md` Phase 11. |
| `AuraTextField` | Text input | Now also `autofocus`, `focusNode` and `prefixIcon`, so a search input does not have to drop to a raw `TextField`. |

## `AuraCommandPalette`

The one component here with real interaction rules, so they are worth stating:

- **A command that cannot run is listed with its reason, not hidden.** `AuraCommandItem` with a
  null `onInvoke` renders greyed, shows its `disabledReason` where the shortcut would be, and
  announces itself as unavailable. Hiding it teaches the user the command does not exist; showing
  it teaches them what to do first.
- **Arrow keys skip disabled entries**, so Enter is never a dead interaction, and stop at the ends
  rather than wrapping — holding an arrow key should not lose your place.
- **Matching is not fuzzy.** Every whitespace-separated token must appear in the command's group
  or title. Subsequence matching would make `tsb` find "Toggle Side Bar", and would also make half
  the palette match almost anything; in a list being scanned under time pressure, predictable
  beats clever.
- **Selection is a filled row *and* a semantics flag**, never colour alone.
- **Escape closes it** through the dialog barrier's own dismiss handling — no binding needed.
- Commands carry their keyboard shortcut. A palette is where somebody learns the shortcut for the
  thing they just did the slow way.

The desktop's own command set is `aura_client/lib/desktop/shell/desktop_commands.dart`, built
fresh each time the palette opens because whether a command can run is a snapshot of that moment.

## Required states

Where a component can be in one of these, it must handle it — and must not signal it with colour
alone:

```text
default · hover · pressed · focused · selected · disabled · loading · error
```

Keyboard focus must be *visible*, not merely present: a `focusColor` or equivalent, so a Tab-only
user can tell what is targeted. Selected and disabled belong in `Semantics` as well as in the
paint.

---

## Not yet componentised

These exist today only as per-screen one-offs, which is the direct cause of the 99 hardcoded
colours and 296 raw font sizes the baseline audit counted (F-04). Listed roughly in order of how
often they are duplicated:

```text
toast             banner           avatar
tree item         tabs             toolbar
context menu      progress bar     dropdown
approval card     agent step       diff summary
activity item     search field     bottom sheet
```

(`status badge` came off this list with `AuraSyncStatusView` — the sync pill was the most
duplicated instance of it, and it was duplicated exactly once, inside chat.)

**Phase 2 scope**, and the order to take them in is by duplication count, not by how interesting
they are — the tree item, the status badge and the toolbar are copied the most.

Two are blocked or constrained rather than merely unscheduled:

- **Approval card** — blocked with Phase 9. The approval payload carries `command`, `root`,
  `paths` and `diff_preview`; the reason, risk and reversibility the card is supposed to show are
  not transmitted at all, and inventing them client-side would be fabrication. See
  `APPROVAL_UX_SPEC.md` once the protocol work starts.
- **Agent step / diff summary** — should follow the AI and agent UX work (Phases 7–8) rather than
  lead it, or they will encode the current chat-shaped model of an agent run.
