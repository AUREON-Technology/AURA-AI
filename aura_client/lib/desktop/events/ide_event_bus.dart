/// IDE event bus for AURA Desktop.
///
/// Provides a stream-based event system for reactive UI updates.
/// Riverpod notifiers consume events to keep state in sync.
library;

import 'dart:async';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import 'ide_event.dart';

/// Event bus for IDE events.
///
/// Uses a broadcast stream so multiple listeners can consume events.
/// Events are published from anywhere in the IDE (editor, explorer, terminal, etc.)
/// and consumed by state notifiers.
class IdeEventBus {
  IdeEventBus() : _controller = StreamController<IdeEvent>.broadcast();

  final StreamController<IdeEvent> _controller;

  /// Stream of all IDE events.
  Stream<IdeEvent> get stream => _controller.stream;

  /// Publishes an event to all listeners.
  void publish(IdeEvent event) {
    if (!_controller.isClosed) {
      _controller.add(event);
    }
  }

  /// Publishes multiple events.
  void publishAll(Iterable<IdeEvent> events) {
    for (final event in events) {
      publish(event);
    }
  }

  /// Closes the event bus.
  Future<void> close() => _controller.close();
}

/// Riverpod provider for the IDE event bus.
final ideEventBusProvider = Provider<IdeEventBus>((ref) {
  final bus = IdeEventBus();
  ref.onDispose(() => bus.close());
  return bus;
});

/// Extension for convenient event publishing.
extension IdeEventBusPublisher on Ref {
  /// Publishes an event to the IDE event bus.
  void publishIdeEvent(IdeEvent event) {
    read(ideEventBusProvider).publish(event);
  }

  /// Publishes multiple events to the IDE event bus.
  void publishIdeEvents(Iterable<IdeEvent> events) {
    read(ideEventBusProvider).publishAll(events);
  }
}