/// Spawns and supervises `aura_hostd run` (ADR-011 Unit 8). See `hostd_supervisor_io.dart` for
/// the real implementation; web gets a stub (no native process to spawn there), same pattern as
/// the rest of `core/hostd/`.
library;

export 'hostd_supervisor_stub.dart' if (dart.library.ffi) 'hostd_supervisor_io.dart';
