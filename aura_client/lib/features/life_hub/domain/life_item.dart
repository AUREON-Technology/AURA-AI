enum LifeItemType { task, event, finance, health }

class LifeItem {
  final String id;
  final LifeItemType type;
  final String title;
  final String? subtitle;
  final DateTime when;
  final bool done;

  const LifeItem({
    required this.id,
    required this.type,
    required this.title,
    required this.when,
    this.subtitle,
    this.done = false,
  });
}
