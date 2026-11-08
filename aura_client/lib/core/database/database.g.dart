// GENERATED CODE - DO NOT MODIFY BY HAND

part of 'database.dart';

// ignore_for_file: type=lint
class $TasksTable extends Tasks with TableInfo<$TasksTable, Task> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $TasksTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> title =
      GeneratedColumn<String>(
        'title',
        aliasedName,
        false,
        additionalChecks: GeneratedColumn.checkTextLength(
          minTextLength: 1,
          maxTextLength: 200,
        ),
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($TasksTable.$convertertitle);
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> description =
      GeneratedColumn<String>(
        'description',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($TasksTable.$converterdescriptionn);
  static const VerificationMeta _priorityMeta = const VerificationMeta(
    'priority',
  );
  @override
  late final GeneratedColumn<String> priority = GeneratedColumn<String>(
    'priority',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _dueDateMeta = const VerificationMeta(
    'dueDate',
  );
  @override
  late final GeneratedColumn<DateTime> dueDate = GeneratedColumn<DateTime>(
    'due_date',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _isCompletedMeta = const VerificationMeta(
    'isCompleted',
  );
  @override
  late final GeneratedColumn<bool> isCompleted = GeneratedColumn<bool>(
    'is_completed',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_completed" IN (0, 1))',
    ),
  );
  static const VerificationMeta _googleTaskIdMeta = const VerificationMeta(
    'googleTaskId',
  );
  @override
  late final GeneratedColumn<String> googleTaskId = GeneratedColumn<String>(
    'google_task_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _categoryMeta = const VerificationMeta(
    'category',
  );
  @override
  late final GeneratedColumn<String> category = GeneratedColumn<String>(
    'category',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    title,
    description,
    priority,
    dueDate,
    isCompleted,
    googleTaskId,
    category,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'tasks';
  @override
  VerificationContext validateIntegrity(
    Insertable<Task> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('priority')) {
      context.handle(
        _priorityMeta,
        priority.isAcceptableOrUnknown(data['priority']!, _priorityMeta),
      );
    } else if (isInserting) {
      context.missing(_priorityMeta);
    }
    if (data.containsKey('due_date')) {
      context.handle(
        _dueDateMeta,
        dueDate.isAcceptableOrUnknown(data['due_date']!, _dueDateMeta),
      );
    }
    if (data.containsKey('is_completed')) {
      context.handle(
        _isCompletedMeta,
        isCompleted.isAcceptableOrUnknown(
          data['is_completed']!,
          _isCompletedMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_isCompletedMeta);
    }
    if (data.containsKey('google_task_id')) {
      context.handle(
        _googleTaskIdMeta,
        googleTaskId.isAcceptableOrUnknown(
          data['google_task_id']!,
          _googleTaskIdMeta,
        ),
      );
    }
    if (data.containsKey('category')) {
      context.handle(
        _categoryMeta,
        category.isAcceptableOrUnknown(data['category']!, _categoryMeta),
      );
    } else if (isInserting) {
      context.missing(_categoryMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  Task map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return Task(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      title: $TasksTable.$convertertitle.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}title'],
        )!,
      ),
      description: $TasksTable.$converterdescriptionn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}description'],
        ),
      ),
      priority: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}priority'],
      )!,
      dueDate: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}due_date'],
      ),
      isCompleted: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_completed'],
      )!,
      googleTaskId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}google_task_id'],
      ),
      category: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}category'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $TasksTable createAlias(String alias) {
    return $TasksTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $convertertitle =
      const EncryptedTextConverter();
  static TypeConverter<String, String> $converterdescription =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $converterdescriptionn =
      NullAwareTypeConverter.wrap($converterdescription);
}

class Task extends DataClass implements Insertable<Task> {
  final String id;
  final String title;
  final String? description;
  final String priority;
  final DateTime? dueDate;
  final bool isCompleted;
  final String? googleTaskId;
  final String category;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const Task({
    required this.id,
    required this.title,
    this.description,
    required this.priority,
    this.dueDate,
    required this.isCompleted,
    this.googleTaskId,
    required this.category,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    {
      map['title'] = Variable<String>($TasksTable.$convertertitle.toSql(title));
    }
    if (!nullToAbsent || description != null) {
      map['description'] = Variable<String>(
        $TasksTable.$converterdescriptionn.toSql(description),
      );
    }
    map['priority'] = Variable<String>(priority);
    if (!nullToAbsent || dueDate != null) {
      map['due_date'] = Variable<DateTime>(dueDate);
    }
    map['is_completed'] = Variable<bool>(isCompleted);
    if (!nullToAbsent || googleTaskId != null) {
      map['google_task_id'] = Variable<String>(googleTaskId);
    }
    map['category'] = Variable<String>(category);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  TasksCompanion toCompanion(bool nullToAbsent) {
    return TasksCompanion(
      id: Value(id),
      title: Value(title),
      description: description == null && nullToAbsent
          ? const Value.absent()
          : Value(description),
      priority: Value(priority),
      dueDate: dueDate == null && nullToAbsent
          ? const Value.absent()
          : Value(dueDate),
      isCompleted: Value(isCompleted),
      googleTaskId: googleTaskId == null && nullToAbsent
          ? const Value.absent()
          : Value(googleTaskId),
      category: Value(category),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory Task.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return Task(
      id: serializer.fromJson<String>(json['id']),
      title: serializer.fromJson<String>(json['title']),
      description: serializer.fromJson<String?>(json['description']),
      priority: serializer.fromJson<String>(json['priority']),
      dueDate: serializer.fromJson<DateTime?>(json['dueDate']),
      isCompleted: serializer.fromJson<bool>(json['isCompleted']),
      googleTaskId: serializer.fromJson<String?>(json['googleTaskId']),
      category: serializer.fromJson<String>(json['category']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'title': serializer.toJson<String>(title),
      'description': serializer.toJson<String?>(description),
      'priority': serializer.toJson<String>(priority),
      'dueDate': serializer.toJson<DateTime?>(dueDate),
      'isCompleted': serializer.toJson<bool>(isCompleted),
      'googleTaskId': serializer.toJson<String?>(googleTaskId),
      'category': serializer.toJson<String>(category),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  Task copyWith({
    String? id,
    String? title,
    Value<String?> description = const Value.absent(),
    String? priority,
    Value<DateTime?> dueDate = const Value.absent(),
    bool? isCompleted,
    Value<String?> googleTaskId = const Value.absent(),
    String? category,
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => Task(
    id: id ?? this.id,
    title: title ?? this.title,
    description: description.present ? description.value : this.description,
    priority: priority ?? this.priority,
    dueDate: dueDate.present ? dueDate.value : this.dueDate,
    isCompleted: isCompleted ?? this.isCompleted,
    googleTaskId: googleTaskId.present ? googleTaskId.value : this.googleTaskId,
    category: category ?? this.category,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  Task copyWithCompanion(TasksCompanion data) {
    return Task(
      id: data.id.present ? data.id.value : this.id,
      title: data.title.present ? data.title.value : this.title,
      description: data.description.present
          ? data.description.value
          : this.description,
      priority: data.priority.present ? data.priority.value : this.priority,
      dueDate: data.dueDate.present ? data.dueDate.value : this.dueDate,
      isCompleted: data.isCompleted.present
          ? data.isCompleted.value
          : this.isCompleted,
      googleTaskId: data.googleTaskId.present
          ? data.googleTaskId.value
          : this.googleTaskId,
      category: data.category.present ? data.category.value : this.category,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('Task(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('priority: $priority, ')
          ..write('dueDate: $dueDate, ')
          ..write('isCompleted: $isCompleted, ')
          ..write('googleTaskId: $googleTaskId, ')
          ..write('category: $category, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    title,
    description,
    priority,
    dueDate,
    isCompleted,
    googleTaskId,
    category,
    createdAt,
    isSynced,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is Task &&
          other.id == this.id &&
          other.title == this.title &&
          other.description == this.description &&
          other.priority == this.priority &&
          other.dueDate == this.dueDate &&
          other.isCompleted == this.isCompleted &&
          other.googleTaskId == this.googleTaskId &&
          other.category == this.category &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class TasksCompanion extends UpdateCompanion<Task> {
  final Value<String> id;
  final Value<String> title;
  final Value<String?> description;
  final Value<String> priority;
  final Value<DateTime?> dueDate;
  final Value<bool> isCompleted;
  final Value<String?> googleTaskId;
  final Value<String> category;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const TasksCompanion({
    this.id = const Value.absent(),
    this.title = const Value.absent(),
    this.description = const Value.absent(),
    this.priority = const Value.absent(),
    this.dueDate = const Value.absent(),
    this.isCompleted = const Value.absent(),
    this.googleTaskId = const Value.absent(),
    this.category = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  TasksCompanion.insert({
    required String id,
    required String title,
    this.description = const Value.absent(),
    required String priority,
    this.dueDate = const Value.absent(),
    required bool isCompleted,
    this.googleTaskId = const Value.absent(),
    required String category,
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       title = Value(title),
       priority = Value(priority),
       isCompleted = Value(isCompleted),
       category = Value(category),
       createdAt = Value(createdAt);
  static Insertable<Task> custom({
    Expression<String>? id,
    Expression<String>? title,
    Expression<String>? description,
    Expression<String>? priority,
    Expression<DateTime>? dueDate,
    Expression<bool>? isCompleted,
    Expression<String>? googleTaskId,
    Expression<String>? category,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (title != null) 'title': title,
      if (description != null) 'description': description,
      if (priority != null) 'priority': priority,
      if (dueDate != null) 'due_date': dueDate,
      if (isCompleted != null) 'is_completed': isCompleted,
      if (googleTaskId != null) 'google_task_id': googleTaskId,
      if (category != null) 'category': category,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  TasksCompanion copyWith({
    Value<String>? id,
    Value<String>? title,
    Value<String?>? description,
    Value<String>? priority,
    Value<DateTime?>? dueDate,
    Value<bool>? isCompleted,
    Value<String?>? googleTaskId,
    Value<String>? category,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return TasksCompanion(
      id: id ?? this.id,
      title: title ?? this.title,
      description: description ?? this.description,
      priority: priority ?? this.priority,
      dueDate: dueDate ?? this.dueDate,
      isCompleted: isCompleted ?? this.isCompleted,
      googleTaskId: googleTaskId ?? this.googleTaskId,
      category: category ?? this.category,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (title.present) {
      map['title'] = Variable<String>(
        $TasksTable.$convertertitle.toSql(title.value),
      );
    }
    if (description.present) {
      map['description'] = Variable<String>(
        $TasksTable.$converterdescriptionn.toSql(description.value),
      );
    }
    if (priority.present) {
      map['priority'] = Variable<String>(priority.value);
    }
    if (dueDate.present) {
      map['due_date'] = Variable<DateTime>(dueDate.value);
    }
    if (isCompleted.present) {
      map['is_completed'] = Variable<bool>(isCompleted.value);
    }
    if (googleTaskId.present) {
      map['google_task_id'] = Variable<String>(googleTaskId.value);
    }
    if (category.present) {
      map['category'] = Variable<String>(category.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('TasksCompanion(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('priority: $priority, ')
          ..write('dueDate: $dueDate, ')
          ..write('isCompleted: $isCompleted, ')
          ..write('googleTaskId: $googleTaskId, ')
          ..write('category: $category, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $CalendarEventsTable extends CalendarEvents
    with TableInfo<$CalendarEventsTable, CalendarEvent> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $CalendarEventsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> title =
      GeneratedColumn<String>(
        'title',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($CalendarEventsTable.$convertertitle);
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> description =
      GeneratedColumn<String>(
        'description',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($CalendarEventsTable.$converterdescriptionn);
  static const VerificationMeta _startTimeMeta = const VerificationMeta(
    'startTime',
  );
  @override
  late final GeneratedColumn<DateTime> startTime = GeneratedColumn<DateTime>(
    'start_time',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _endTimeMeta = const VerificationMeta(
    'endTime',
  );
  @override
  late final GeneratedColumn<DateTime> endTime = GeneratedColumn<DateTime>(
    'end_time',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _googleEventIdMeta = const VerificationMeta(
    'googleEventId',
  );
  @override
  late final GeneratedColumn<String> googleEventId = GeneratedColumn<String>(
    'google_event_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _isAllDayMeta = const VerificationMeta(
    'isAllDay',
  );
  @override
  late final GeneratedColumn<bool> isAllDay = GeneratedColumn<bool>(
    'is_all_day',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_all_day" IN (0, 1))',
    ),
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    title,
    description,
    startTime,
    endTime,
    googleEventId,
    isAllDay,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'calendar_events';
  @override
  VerificationContext validateIntegrity(
    Insertable<CalendarEvent> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('start_time')) {
      context.handle(
        _startTimeMeta,
        startTime.isAcceptableOrUnknown(data['start_time']!, _startTimeMeta),
      );
    } else if (isInserting) {
      context.missing(_startTimeMeta);
    }
    if (data.containsKey('end_time')) {
      context.handle(
        _endTimeMeta,
        endTime.isAcceptableOrUnknown(data['end_time']!, _endTimeMeta),
      );
    } else if (isInserting) {
      context.missing(_endTimeMeta);
    }
    if (data.containsKey('google_event_id')) {
      context.handle(
        _googleEventIdMeta,
        googleEventId.isAcceptableOrUnknown(
          data['google_event_id']!,
          _googleEventIdMeta,
        ),
      );
    }
    if (data.containsKey('is_all_day')) {
      context.handle(
        _isAllDayMeta,
        isAllDay.isAcceptableOrUnknown(data['is_all_day']!, _isAllDayMeta),
      );
    } else if (isInserting) {
      context.missing(_isAllDayMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  CalendarEvent map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return CalendarEvent(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      title: $CalendarEventsTable.$convertertitle.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}title'],
        )!,
      ),
      description: $CalendarEventsTable.$converterdescriptionn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}description'],
        ),
      ),
      startTime: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}start_time'],
      )!,
      endTime: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}end_time'],
      )!,
      googleEventId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}google_event_id'],
      ),
      isAllDay: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_all_day'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $CalendarEventsTable createAlias(String alias) {
    return $CalendarEventsTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $convertertitle =
      const EncryptedTextConverter();
  static TypeConverter<String, String> $converterdescription =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $converterdescriptionn =
      NullAwareTypeConverter.wrap($converterdescription);
}

class CalendarEvent extends DataClass implements Insertable<CalendarEvent> {
  final String id;
  final String title;
  final String? description;
  final DateTime startTime;
  final DateTime endTime;
  final String? googleEventId;
  final bool isAllDay;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const CalendarEvent({
    required this.id,
    required this.title,
    this.description,
    required this.startTime,
    required this.endTime,
    this.googleEventId,
    required this.isAllDay,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    {
      map['title'] = Variable<String>(
        $CalendarEventsTable.$convertertitle.toSql(title),
      );
    }
    if (!nullToAbsent || description != null) {
      map['description'] = Variable<String>(
        $CalendarEventsTable.$converterdescriptionn.toSql(description),
      );
    }
    map['start_time'] = Variable<DateTime>(startTime);
    map['end_time'] = Variable<DateTime>(endTime);
    if (!nullToAbsent || googleEventId != null) {
      map['google_event_id'] = Variable<String>(googleEventId);
    }
    map['is_all_day'] = Variable<bool>(isAllDay);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  CalendarEventsCompanion toCompanion(bool nullToAbsent) {
    return CalendarEventsCompanion(
      id: Value(id),
      title: Value(title),
      description: description == null && nullToAbsent
          ? const Value.absent()
          : Value(description),
      startTime: Value(startTime),
      endTime: Value(endTime),
      googleEventId: googleEventId == null && nullToAbsent
          ? const Value.absent()
          : Value(googleEventId),
      isAllDay: Value(isAllDay),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory CalendarEvent.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return CalendarEvent(
      id: serializer.fromJson<String>(json['id']),
      title: serializer.fromJson<String>(json['title']),
      description: serializer.fromJson<String?>(json['description']),
      startTime: serializer.fromJson<DateTime>(json['startTime']),
      endTime: serializer.fromJson<DateTime>(json['endTime']),
      googleEventId: serializer.fromJson<String?>(json['googleEventId']),
      isAllDay: serializer.fromJson<bool>(json['isAllDay']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'title': serializer.toJson<String>(title),
      'description': serializer.toJson<String?>(description),
      'startTime': serializer.toJson<DateTime>(startTime),
      'endTime': serializer.toJson<DateTime>(endTime),
      'googleEventId': serializer.toJson<String?>(googleEventId),
      'isAllDay': serializer.toJson<bool>(isAllDay),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  CalendarEvent copyWith({
    String? id,
    String? title,
    Value<String?> description = const Value.absent(),
    DateTime? startTime,
    DateTime? endTime,
    Value<String?> googleEventId = const Value.absent(),
    bool? isAllDay,
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => CalendarEvent(
    id: id ?? this.id,
    title: title ?? this.title,
    description: description.present ? description.value : this.description,
    startTime: startTime ?? this.startTime,
    endTime: endTime ?? this.endTime,
    googleEventId: googleEventId.present
        ? googleEventId.value
        : this.googleEventId,
    isAllDay: isAllDay ?? this.isAllDay,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  CalendarEvent copyWithCompanion(CalendarEventsCompanion data) {
    return CalendarEvent(
      id: data.id.present ? data.id.value : this.id,
      title: data.title.present ? data.title.value : this.title,
      description: data.description.present
          ? data.description.value
          : this.description,
      startTime: data.startTime.present ? data.startTime.value : this.startTime,
      endTime: data.endTime.present ? data.endTime.value : this.endTime,
      googleEventId: data.googleEventId.present
          ? data.googleEventId.value
          : this.googleEventId,
      isAllDay: data.isAllDay.present ? data.isAllDay.value : this.isAllDay,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('CalendarEvent(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('startTime: $startTime, ')
          ..write('endTime: $endTime, ')
          ..write('googleEventId: $googleEventId, ')
          ..write('isAllDay: $isAllDay, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    title,
    description,
    startTime,
    endTime,
    googleEventId,
    isAllDay,
    createdAt,
    isSynced,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is CalendarEvent &&
          other.id == this.id &&
          other.title == this.title &&
          other.description == this.description &&
          other.startTime == this.startTime &&
          other.endTime == this.endTime &&
          other.googleEventId == this.googleEventId &&
          other.isAllDay == this.isAllDay &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class CalendarEventsCompanion extends UpdateCompanion<CalendarEvent> {
  final Value<String> id;
  final Value<String> title;
  final Value<String?> description;
  final Value<DateTime> startTime;
  final Value<DateTime> endTime;
  final Value<String?> googleEventId;
  final Value<bool> isAllDay;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const CalendarEventsCompanion({
    this.id = const Value.absent(),
    this.title = const Value.absent(),
    this.description = const Value.absent(),
    this.startTime = const Value.absent(),
    this.endTime = const Value.absent(),
    this.googleEventId = const Value.absent(),
    this.isAllDay = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  CalendarEventsCompanion.insert({
    required String id,
    required String title,
    this.description = const Value.absent(),
    required DateTime startTime,
    required DateTime endTime,
    this.googleEventId = const Value.absent(),
    required bool isAllDay,
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       title = Value(title),
       startTime = Value(startTime),
       endTime = Value(endTime),
       isAllDay = Value(isAllDay),
       createdAt = Value(createdAt);
  static Insertable<CalendarEvent> custom({
    Expression<String>? id,
    Expression<String>? title,
    Expression<String>? description,
    Expression<DateTime>? startTime,
    Expression<DateTime>? endTime,
    Expression<String>? googleEventId,
    Expression<bool>? isAllDay,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (title != null) 'title': title,
      if (description != null) 'description': description,
      if (startTime != null) 'start_time': startTime,
      if (endTime != null) 'end_time': endTime,
      if (googleEventId != null) 'google_event_id': googleEventId,
      if (isAllDay != null) 'is_all_day': isAllDay,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  CalendarEventsCompanion copyWith({
    Value<String>? id,
    Value<String>? title,
    Value<String?>? description,
    Value<DateTime>? startTime,
    Value<DateTime>? endTime,
    Value<String?>? googleEventId,
    Value<bool>? isAllDay,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return CalendarEventsCompanion(
      id: id ?? this.id,
      title: title ?? this.title,
      description: description ?? this.description,
      startTime: startTime ?? this.startTime,
      endTime: endTime ?? this.endTime,
      googleEventId: googleEventId ?? this.googleEventId,
      isAllDay: isAllDay ?? this.isAllDay,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (title.present) {
      map['title'] = Variable<String>(
        $CalendarEventsTable.$convertertitle.toSql(title.value),
      );
    }
    if (description.present) {
      map['description'] = Variable<String>(
        $CalendarEventsTable.$converterdescriptionn.toSql(description.value),
      );
    }
    if (startTime.present) {
      map['start_time'] = Variable<DateTime>(startTime.value);
    }
    if (endTime.present) {
      map['end_time'] = Variable<DateTime>(endTime.value);
    }
    if (googleEventId.present) {
      map['google_event_id'] = Variable<String>(googleEventId.value);
    }
    if (isAllDay.present) {
      map['is_all_day'] = Variable<bool>(isAllDay.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('CalendarEventsCompanion(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('startTime: $startTime, ')
          ..write('endTime: $endTime, ')
          ..write('googleEventId: $googleEventId, ')
          ..write('isAllDay: $isAllDay, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $FinanceRecordsTable extends FinanceRecords
    with TableInfo<$FinanceRecordsTable, FinanceRecord> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $FinanceRecordsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _typeMeta = const VerificationMeta('type');
  @override
  late final GeneratedColumn<String> type = GeneratedColumn<String>(
    'type',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _amountMeta = const VerificationMeta('amount');
  @override
  late final GeneratedColumn<double> amount = GeneratedColumn<double>(
    'amount',
    aliasedName,
    false,
    type: DriftSqlType.double,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _categoryMeta = const VerificationMeta(
    'category',
  );
  @override
  late final GeneratedColumn<String> category = GeneratedColumn<String>(
    'category',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> description =
      GeneratedColumn<String>(
        'description',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($FinanceRecordsTable.$converterdescriptionn);
  static const VerificationMeta _dateMeta = const VerificationMeta('date');
  @override
  late final GeneratedColumn<DateTime> date = GeneratedColumn<DateTime>(
    'date',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    type,
    amount,
    category,
    description,
    date,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'finance_records';
  @override
  VerificationContext validateIntegrity(
    Insertable<FinanceRecord> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('type')) {
      context.handle(
        _typeMeta,
        type.isAcceptableOrUnknown(data['type']!, _typeMeta),
      );
    } else if (isInserting) {
      context.missing(_typeMeta);
    }
    if (data.containsKey('amount')) {
      context.handle(
        _amountMeta,
        amount.isAcceptableOrUnknown(data['amount']!, _amountMeta),
      );
    } else if (isInserting) {
      context.missing(_amountMeta);
    }
    if (data.containsKey('category')) {
      context.handle(
        _categoryMeta,
        category.isAcceptableOrUnknown(data['category']!, _categoryMeta),
      );
    } else if (isInserting) {
      context.missing(_categoryMeta);
    }
    if (data.containsKey('date')) {
      context.handle(
        _dateMeta,
        date.isAcceptableOrUnknown(data['date']!, _dateMeta),
      );
    } else if (isInserting) {
      context.missing(_dateMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  FinanceRecord map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return FinanceRecord(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      type: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}type'],
      )!,
      amount: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}amount'],
      )!,
      category: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}category'],
      )!,
      description: $FinanceRecordsTable.$converterdescriptionn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}description'],
        ),
      ),
      date: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}date'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $FinanceRecordsTable createAlias(String alias) {
    return $FinanceRecordsTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $converterdescription =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $converterdescriptionn =
      NullAwareTypeConverter.wrap($converterdescription);
}

class FinanceRecord extends DataClass implements Insertable<FinanceRecord> {
  final String id;
  final String type;
  final double amount;
  final String category;
  final String? description;
  final DateTime date;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const FinanceRecord({
    required this.id,
    required this.type,
    required this.amount,
    required this.category,
    this.description,
    required this.date,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['type'] = Variable<String>(type);
    map['amount'] = Variable<double>(amount);
    map['category'] = Variable<String>(category);
    if (!nullToAbsent || description != null) {
      map['description'] = Variable<String>(
        $FinanceRecordsTable.$converterdescriptionn.toSql(description),
      );
    }
    map['date'] = Variable<DateTime>(date);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  FinanceRecordsCompanion toCompanion(bool nullToAbsent) {
    return FinanceRecordsCompanion(
      id: Value(id),
      type: Value(type),
      amount: Value(amount),
      category: Value(category),
      description: description == null && nullToAbsent
          ? const Value.absent()
          : Value(description),
      date: Value(date),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory FinanceRecord.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return FinanceRecord(
      id: serializer.fromJson<String>(json['id']),
      type: serializer.fromJson<String>(json['type']),
      amount: serializer.fromJson<double>(json['amount']),
      category: serializer.fromJson<String>(json['category']),
      description: serializer.fromJson<String?>(json['description']),
      date: serializer.fromJson<DateTime>(json['date']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'type': serializer.toJson<String>(type),
      'amount': serializer.toJson<double>(amount),
      'category': serializer.toJson<String>(category),
      'description': serializer.toJson<String?>(description),
      'date': serializer.toJson<DateTime>(date),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  FinanceRecord copyWith({
    String? id,
    String? type,
    double? amount,
    String? category,
    Value<String?> description = const Value.absent(),
    DateTime? date,
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => FinanceRecord(
    id: id ?? this.id,
    type: type ?? this.type,
    amount: amount ?? this.amount,
    category: category ?? this.category,
    description: description.present ? description.value : this.description,
    date: date ?? this.date,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  FinanceRecord copyWithCompanion(FinanceRecordsCompanion data) {
    return FinanceRecord(
      id: data.id.present ? data.id.value : this.id,
      type: data.type.present ? data.type.value : this.type,
      amount: data.amount.present ? data.amount.value : this.amount,
      category: data.category.present ? data.category.value : this.category,
      description: data.description.present
          ? data.description.value
          : this.description,
      date: data.date.present ? data.date.value : this.date,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('FinanceRecord(')
          ..write('id: $id, ')
          ..write('type: $type, ')
          ..write('amount: $amount, ')
          ..write('category: $category, ')
          ..write('description: $description, ')
          ..write('date: $date, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    type,
    amount,
    category,
    description,
    date,
    createdAt,
    isSynced,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is FinanceRecord &&
          other.id == this.id &&
          other.type == this.type &&
          other.amount == this.amount &&
          other.category == this.category &&
          other.description == this.description &&
          other.date == this.date &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class FinanceRecordsCompanion extends UpdateCompanion<FinanceRecord> {
  final Value<String> id;
  final Value<String> type;
  final Value<double> amount;
  final Value<String> category;
  final Value<String?> description;
  final Value<DateTime> date;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const FinanceRecordsCompanion({
    this.id = const Value.absent(),
    this.type = const Value.absent(),
    this.amount = const Value.absent(),
    this.category = const Value.absent(),
    this.description = const Value.absent(),
    this.date = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  FinanceRecordsCompanion.insert({
    required String id,
    required String type,
    required double amount,
    required String category,
    this.description = const Value.absent(),
    required DateTime date,
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       type = Value(type),
       amount = Value(amount),
       category = Value(category),
       date = Value(date),
       createdAt = Value(createdAt);
  static Insertable<FinanceRecord> custom({
    Expression<String>? id,
    Expression<String>? type,
    Expression<double>? amount,
    Expression<String>? category,
    Expression<String>? description,
    Expression<DateTime>? date,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (type != null) 'type': type,
      if (amount != null) 'amount': amount,
      if (category != null) 'category': category,
      if (description != null) 'description': description,
      if (date != null) 'date': date,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  FinanceRecordsCompanion copyWith({
    Value<String>? id,
    Value<String>? type,
    Value<double>? amount,
    Value<String>? category,
    Value<String?>? description,
    Value<DateTime>? date,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return FinanceRecordsCompanion(
      id: id ?? this.id,
      type: type ?? this.type,
      amount: amount ?? this.amount,
      category: category ?? this.category,
      description: description ?? this.description,
      date: date ?? this.date,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (type.present) {
      map['type'] = Variable<String>(type.value);
    }
    if (amount.present) {
      map['amount'] = Variable<double>(amount.value);
    }
    if (category.present) {
      map['category'] = Variable<String>(category.value);
    }
    if (description.present) {
      map['description'] = Variable<String>(
        $FinanceRecordsTable.$converterdescriptionn.toSql(description.value),
      );
    }
    if (date.present) {
      map['date'] = Variable<DateTime>(date.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('FinanceRecordsCompanion(')
          ..write('id: $id, ')
          ..write('type: $type, ')
          ..write('amount: $amount, ')
          ..write('category: $category, ')
          ..write('description: $description, ')
          ..write('date: $date, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $HealthRecordsTable extends HealthRecords
    with TableInfo<$HealthRecordsTable, HealthRecord> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $HealthRecordsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _recordDateMeta = const VerificationMeta(
    'recordDate',
  );
  @override
  late final GeneratedColumn<DateTime> recordDate = GeneratedColumn<DateTime>(
    'record_date',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _weightMeta = const VerificationMeta('weight');
  @override
  late final GeneratedColumn<double> weight = GeneratedColumn<double>(
    'weight',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _waterIntakeMlMeta = const VerificationMeta(
    'waterIntakeMl',
  );
  @override
  late final GeneratedColumn<int> waterIntakeMl = GeneratedColumn<int>(
    'water_intake_ml',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _sleepMinutesMeta = const VerificationMeta(
    'sleepMinutes',
  );
  @override
  late final GeneratedColumn<int> sleepMinutes = GeneratedColumn<int>(
    'sleep_minutes',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _caloriesConsumedMeta = const VerificationMeta(
    'caloriesConsumed',
  );
  @override
  late final GeneratedColumn<int> caloriesConsumed = GeneratedColumn<int>(
    'calories_consumed',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _exerciseMinutesMeta = const VerificationMeta(
    'exerciseMinutes',
  );
  @override
  late final GeneratedColumn<int> exerciseMinutes = GeneratedColumn<int>(
    'exercise_minutes',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    recordDate,
    weight,
    waterIntakeMl,
    sleepMinutes,
    caloriesConsumed,
    exerciseMinutes,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'health_records';
  @override
  VerificationContext validateIntegrity(
    Insertable<HealthRecord> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('record_date')) {
      context.handle(
        _recordDateMeta,
        recordDate.isAcceptableOrUnknown(data['record_date']!, _recordDateMeta),
      );
    } else if (isInserting) {
      context.missing(_recordDateMeta);
    }
    if (data.containsKey('weight')) {
      context.handle(
        _weightMeta,
        weight.isAcceptableOrUnknown(data['weight']!, _weightMeta),
      );
    }
    if (data.containsKey('water_intake_ml')) {
      context.handle(
        _waterIntakeMlMeta,
        waterIntakeMl.isAcceptableOrUnknown(
          data['water_intake_ml']!,
          _waterIntakeMlMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_waterIntakeMlMeta);
    }
    if (data.containsKey('sleep_minutes')) {
      context.handle(
        _sleepMinutesMeta,
        sleepMinutes.isAcceptableOrUnknown(
          data['sleep_minutes']!,
          _sleepMinutesMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_sleepMinutesMeta);
    }
    if (data.containsKey('calories_consumed')) {
      context.handle(
        _caloriesConsumedMeta,
        caloriesConsumed.isAcceptableOrUnknown(
          data['calories_consumed']!,
          _caloriesConsumedMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_caloriesConsumedMeta);
    }
    if (data.containsKey('exercise_minutes')) {
      context.handle(
        _exerciseMinutesMeta,
        exerciseMinutes.isAcceptableOrUnknown(
          data['exercise_minutes']!,
          _exerciseMinutesMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_exerciseMinutesMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  HealthRecord map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return HealthRecord(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      recordDate: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}record_date'],
      )!,
      weight: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}weight'],
      ),
      waterIntakeMl: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}water_intake_ml'],
      )!,
      sleepMinutes: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}sleep_minutes'],
      )!,
      caloriesConsumed: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}calories_consumed'],
      )!,
      exerciseMinutes: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}exercise_minutes'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $HealthRecordsTable createAlias(String alias) {
    return $HealthRecordsTable(attachedDatabase, alias);
  }
}

class HealthRecord extends DataClass implements Insertable<HealthRecord> {
  final String id;
  final DateTime recordDate;
  final double? weight;
  final int waterIntakeMl;
  final int sleepMinutes;
  final int caloriesConsumed;
  final int exerciseMinutes;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const HealthRecord({
    required this.id,
    required this.recordDate,
    this.weight,
    required this.waterIntakeMl,
    required this.sleepMinutes,
    required this.caloriesConsumed,
    required this.exerciseMinutes,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['record_date'] = Variable<DateTime>(recordDate);
    if (!nullToAbsent || weight != null) {
      map['weight'] = Variable<double>(weight);
    }
    map['water_intake_ml'] = Variable<int>(waterIntakeMl);
    map['sleep_minutes'] = Variable<int>(sleepMinutes);
    map['calories_consumed'] = Variable<int>(caloriesConsumed);
    map['exercise_minutes'] = Variable<int>(exerciseMinutes);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  HealthRecordsCompanion toCompanion(bool nullToAbsent) {
    return HealthRecordsCompanion(
      id: Value(id),
      recordDate: Value(recordDate),
      weight: weight == null && nullToAbsent
          ? const Value.absent()
          : Value(weight),
      waterIntakeMl: Value(waterIntakeMl),
      sleepMinutes: Value(sleepMinutes),
      caloriesConsumed: Value(caloriesConsumed),
      exerciseMinutes: Value(exerciseMinutes),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory HealthRecord.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return HealthRecord(
      id: serializer.fromJson<String>(json['id']),
      recordDate: serializer.fromJson<DateTime>(json['recordDate']),
      weight: serializer.fromJson<double?>(json['weight']),
      waterIntakeMl: serializer.fromJson<int>(json['waterIntakeMl']),
      sleepMinutes: serializer.fromJson<int>(json['sleepMinutes']),
      caloriesConsumed: serializer.fromJson<int>(json['caloriesConsumed']),
      exerciseMinutes: serializer.fromJson<int>(json['exerciseMinutes']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'recordDate': serializer.toJson<DateTime>(recordDate),
      'weight': serializer.toJson<double?>(weight),
      'waterIntakeMl': serializer.toJson<int>(waterIntakeMl),
      'sleepMinutes': serializer.toJson<int>(sleepMinutes),
      'caloriesConsumed': serializer.toJson<int>(caloriesConsumed),
      'exerciseMinutes': serializer.toJson<int>(exerciseMinutes),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  HealthRecord copyWith({
    String? id,
    DateTime? recordDate,
    Value<double?> weight = const Value.absent(),
    int? waterIntakeMl,
    int? sleepMinutes,
    int? caloriesConsumed,
    int? exerciseMinutes,
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => HealthRecord(
    id: id ?? this.id,
    recordDate: recordDate ?? this.recordDate,
    weight: weight.present ? weight.value : this.weight,
    waterIntakeMl: waterIntakeMl ?? this.waterIntakeMl,
    sleepMinutes: sleepMinutes ?? this.sleepMinutes,
    caloriesConsumed: caloriesConsumed ?? this.caloriesConsumed,
    exerciseMinutes: exerciseMinutes ?? this.exerciseMinutes,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  HealthRecord copyWithCompanion(HealthRecordsCompanion data) {
    return HealthRecord(
      id: data.id.present ? data.id.value : this.id,
      recordDate: data.recordDate.present
          ? data.recordDate.value
          : this.recordDate,
      weight: data.weight.present ? data.weight.value : this.weight,
      waterIntakeMl: data.waterIntakeMl.present
          ? data.waterIntakeMl.value
          : this.waterIntakeMl,
      sleepMinutes: data.sleepMinutes.present
          ? data.sleepMinutes.value
          : this.sleepMinutes,
      caloriesConsumed: data.caloriesConsumed.present
          ? data.caloriesConsumed.value
          : this.caloriesConsumed,
      exerciseMinutes: data.exerciseMinutes.present
          ? data.exerciseMinutes.value
          : this.exerciseMinutes,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('HealthRecord(')
          ..write('id: $id, ')
          ..write('recordDate: $recordDate, ')
          ..write('weight: $weight, ')
          ..write('waterIntakeMl: $waterIntakeMl, ')
          ..write('sleepMinutes: $sleepMinutes, ')
          ..write('caloriesConsumed: $caloriesConsumed, ')
          ..write('exerciseMinutes: $exerciseMinutes, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    recordDate,
    weight,
    waterIntakeMl,
    sleepMinutes,
    caloriesConsumed,
    exerciseMinutes,
    createdAt,
    isSynced,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is HealthRecord &&
          other.id == this.id &&
          other.recordDate == this.recordDate &&
          other.weight == this.weight &&
          other.waterIntakeMl == this.waterIntakeMl &&
          other.sleepMinutes == this.sleepMinutes &&
          other.caloriesConsumed == this.caloriesConsumed &&
          other.exerciseMinutes == this.exerciseMinutes &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class HealthRecordsCompanion extends UpdateCompanion<HealthRecord> {
  final Value<String> id;
  final Value<DateTime> recordDate;
  final Value<double?> weight;
  final Value<int> waterIntakeMl;
  final Value<int> sleepMinutes;
  final Value<int> caloriesConsumed;
  final Value<int> exerciseMinutes;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const HealthRecordsCompanion({
    this.id = const Value.absent(),
    this.recordDate = const Value.absent(),
    this.weight = const Value.absent(),
    this.waterIntakeMl = const Value.absent(),
    this.sleepMinutes = const Value.absent(),
    this.caloriesConsumed = const Value.absent(),
    this.exerciseMinutes = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  HealthRecordsCompanion.insert({
    required String id,
    required DateTime recordDate,
    this.weight = const Value.absent(),
    required int waterIntakeMl,
    required int sleepMinutes,
    required int caloriesConsumed,
    required int exerciseMinutes,
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       recordDate = Value(recordDate),
       waterIntakeMl = Value(waterIntakeMl),
       sleepMinutes = Value(sleepMinutes),
       caloriesConsumed = Value(caloriesConsumed),
       exerciseMinutes = Value(exerciseMinutes),
       createdAt = Value(createdAt);
  static Insertable<HealthRecord> custom({
    Expression<String>? id,
    Expression<DateTime>? recordDate,
    Expression<double>? weight,
    Expression<int>? waterIntakeMl,
    Expression<int>? sleepMinutes,
    Expression<int>? caloriesConsumed,
    Expression<int>? exerciseMinutes,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (recordDate != null) 'record_date': recordDate,
      if (weight != null) 'weight': weight,
      if (waterIntakeMl != null) 'water_intake_ml': waterIntakeMl,
      if (sleepMinutes != null) 'sleep_minutes': sleepMinutes,
      if (caloriesConsumed != null) 'calories_consumed': caloriesConsumed,
      if (exerciseMinutes != null) 'exercise_minutes': exerciseMinutes,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  HealthRecordsCompanion copyWith({
    Value<String>? id,
    Value<DateTime>? recordDate,
    Value<double?>? weight,
    Value<int>? waterIntakeMl,
    Value<int>? sleepMinutes,
    Value<int>? caloriesConsumed,
    Value<int>? exerciseMinutes,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return HealthRecordsCompanion(
      id: id ?? this.id,
      recordDate: recordDate ?? this.recordDate,
      weight: weight ?? this.weight,
      waterIntakeMl: waterIntakeMl ?? this.waterIntakeMl,
      sleepMinutes: sleepMinutes ?? this.sleepMinutes,
      caloriesConsumed: caloriesConsumed ?? this.caloriesConsumed,
      exerciseMinutes: exerciseMinutes ?? this.exerciseMinutes,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (recordDate.present) {
      map['record_date'] = Variable<DateTime>(recordDate.value);
    }
    if (weight.present) {
      map['weight'] = Variable<double>(weight.value);
    }
    if (waterIntakeMl.present) {
      map['water_intake_ml'] = Variable<int>(waterIntakeMl.value);
    }
    if (sleepMinutes.present) {
      map['sleep_minutes'] = Variable<int>(sleepMinutes.value);
    }
    if (caloriesConsumed.present) {
      map['calories_consumed'] = Variable<int>(caloriesConsumed.value);
    }
    if (exerciseMinutes.present) {
      map['exercise_minutes'] = Variable<int>(exerciseMinutes.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('HealthRecordsCompanion(')
          ..write('id: $id, ')
          ..write('recordDate: $recordDate, ')
          ..write('weight: $weight, ')
          ..write('waterIntakeMl: $waterIntakeMl, ')
          ..write('sleepMinutes: $sleepMinutes, ')
          ..write('caloriesConsumed: $caloriesConsumed, ')
          ..write('exerciseMinutes: $exerciseMinutes, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $MessagesTable extends Messages with TableInfo<$MessagesTable, Message> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $MessagesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _conversationIdMeta = const VerificationMeta(
    'conversationId',
  );
  @override
  late final GeneratedColumn<String> conversationId = GeneratedColumn<String>(
    'conversation_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _roleMeta = const VerificationMeta('role');
  @override
  late final GeneratedColumn<String> role = GeneratedColumn<String>(
    'role',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> content =
      GeneratedColumn<String>(
        'content',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($MessagesTable.$convertercontent);
  static const VerificationMeta _modelUsedMeta = const VerificationMeta(
    'modelUsed',
  );
  @override
  late final GeneratedColumn<String> modelUsed = GeneratedColumn<String>(
    'model_used',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _tokensUsedMeta = const VerificationMeta(
    'tokensUsed',
  );
  @override
  late final GeneratedColumn<int> tokensUsed = GeneratedColumn<int>(
    'tokens_used',
    aliasedName,
    true,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> citationsJson =
      GeneratedColumn<String>(
        'citations_json',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($MessagesTable.$convertercitationsJsonn);
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> toolCallsJson =
      GeneratedColumn<String>(
        'tool_calls_json',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($MessagesTable.$convertertoolCallsJsonn);
  static const VerificationMeta _agentIdMeta = const VerificationMeta(
    'agentId',
  );
  @override
  late final GeneratedColumn<String> agentId = GeneratedColumn<String>(
    'agent_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _turnIdMeta = const VerificationMeta('turnId');
  @override
  late final GeneratedColumn<String> turnId = GeneratedColumn<String>(
    'turn_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> turnMetaJson =
      GeneratedColumn<String>(
        'turn_meta_json',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($MessagesTable.$converterturnMetaJsonn);
  @override
  List<GeneratedColumn> get $columns => [
    id,
    conversationId,
    role,
    content,
    modelUsed,
    tokensUsed,
    createdAt,
    citationsJson,
    toolCallsJson,
    agentId,
    updatedAt,
    turnId,
    turnMetaJson,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'messages';
  @override
  VerificationContext validateIntegrity(
    Insertable<Message> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('conversation_id')) {
      context.handle(
        _conversationIdMeta,
        conversationId.isAcceptableOrUnknown(
          data['conversation_id']!,
          _conversationIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_conversationIdMeta);
    }
    if (data.containsKey('role')) {
      context.handle(
        _roleMeta,
        role.isAcceptableOrUnknown(data['role']!, _roleMeta),
      );
    } else if (isInserting) {
      context.missing(_roleMeta);
    }
    if (data.containsKey('model_used')) {
      context.handle(
        _modelUsedMeta,
        modelUsed.isAcceptableOrUnknown(data['model_used']!, _modelUsedMeta),
      );
    }
    if (data.containsKey('tokens_used')) {
      context.handle(
        _tokensUsedMeta,
        tokensUsed.isAcceptableOrUnknown(data['tokens_used']!, _tokensUsedMeta),
      );
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('agent_id')) {
      context.handle(
        _agentIdMeta,
        agentId.isAcceptableOrUnknown(data['agent_id']!, _agentIdMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    if (data.containsKey('turn_id')) {
      context.handle(
        _turnIdMeta,
        turnId.isAcceptableOrUnknown(data['turn_id']!, _turnIdMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  Message map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return Message(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      conversationId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}conversation_id'],
      )!,
      role: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}role'],
      )!,
      content: $MessagesTable.$convertercontent.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}content'],
        )!,
      ),
      modelUsed: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}model_used'],
      ),
      tokensUsed: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}tokens_used'],
      ),
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      citationsJson: $MessagesTable.$convertercitationsJsonn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}citations_json'],
        ),
      ),
      toolCallsJson: $MessagesTable.$convertertoolCallsJsonn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}tool_calls_json'],
        ),
      ),
      agentId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}agent_id'],
      ),
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
      turnId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}turn_id'],
      ),
      turnMetaJson: $MessagesTable.$converterturnMetaJsonn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}turn_meta_json'],
        ),
      ),
    );
  }

  @override
  $MessagesTable createAlias(String alias) {
    return $MessagesTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $convertercontent =
      const EncryptedTextConverter();
  static TypeConverter<String, String> $convertercitationsJson =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $convertercitationsJsonn =
      NullAwareTypeConverter.wrap($convertercitationsJson);
  static TypeConverter<String, String> $convertertoolCallsJson =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $convertertoolCallsJsonn =
      NullAwareTypeConverter.wrap($convertertoolCallsJson);
  static TypeConverter<String, String> $converterturnMetaJson =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $converterturnMetaJsonn =
      NullAwareTypeConverter.wrap($converterturnMetaJson);
}

class Message extends DataClass implements Insertable<Message> {
  final String id;
  final String conversationId;
  final String role;
  final String content;
  final String? modelUsed;
  final int? tokensUsed;
  final DateTime createdAt;
  final String? citationsJson;
  final String? toolCallsJson;
  final String? agentId;
  final DateTime? updatedAt;
  final String? turnId;
  final String? turnMetaJson;
  const Message({
    required this.id,
    required this.conversationId,
    required this.role,
    required this.content,
    this.modelUsed,
    this.tokensUsed,
    required this.createdAt,
    this.citationsJson,
    this.toolCallsJson,
    this.agentId,
    this.updatedAt,
    this.turnId,
    this.turnMetaJson,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['conversation_id'] = Variable<String>(conversationId);
    map['role'] = Variable<String>(role);
    {
      map['content'] = Variable<String>(
        $MessagesTable.$convertercontent.toSql(content),
      );
    }
    if (!nullToAbsent || modelUsed != null) {
      map['model_used'] = Variable<String>(modelUsed);
    }
    if (!nullToAbsent || tokensUsed != null) {
      map['tokens_used'] = Variable<int>(tokensUsed);
    }
    map['created_at'] = Variable<DateTime>(createdAt);
    if (!nullToAbsent || citationsJson != null) {
      map['citations_json'] = Variable<String>(
        $MessagesTable.$convertercitationsJsonn.toSql(citationsJson),
      );
    }
    if (!nullToAbsent || toolCallsJson != null) {
      map['tool_calls_json'] = Variable<String>(
        $MessagesTable.$convertertoolCallsJsonn.toSql(toolCallsJson),
      );
    }
    if (!nullToAbsent || agentId != null) {
      map['agent_id'] = Variable<String>(agentId);
    }
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    if (!nullToAbsent || turnId != null) {
      map['turn_id'] = Variable<String>(turnId);
    }
    if (!nullToAbsent || turnMetaJson != null) {
      map['turn_meta_json'] = Variable<String>(
        $MessagesTable.$converterturnMetaJsonn.toSql(turnMetaJson),
      );
    }
    return map;
  }

  MessagesCompanion toCompanion(bool nullToAbsent) {
    return MessagesCompanion(
      id: Value(id),
      conversationId: Value(conversationId),
      role: Value(role),
      content: Value(content),
      modelUsed: modelUsed == null && nullToAbsent
          ? const Value.absent()
          : Value(modelUsed),
      tokensUsed: tokensUsed == null && nullToAbsent
          ? const Value.absent()
          : Value(tokensUsed),
      createdAt: Value(createdAt),
      citationsJson: citationsJson == null && nullToAbsent
          ? const Value.absent()
          : Value(citationsJson),
      toolCallsJson: toolCallsJson == null && nullToAbsent
          ? const Value.absent()
          : Value(toolCallsJson),
      agentId: agentId == null && nullToAbsent
          ? const Value.absent()
          : Value(agentId),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
      turnId: turnId == null && nullToAbsent
          ? const Value.absent()
          : Value(turnId),
      turnMetaJson: turnMetaJson == null && nullToAbsent
          ? const Value.absent()
          : Value(turnMetaJson),
    );
  }

  factory Message.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return Message(
      id: serializer.fromJson<String>(json['id']),
      conversationId: serializer.fromJson<String>(json['conversationId']),
      role: serializer.fromJson<String>(json['role']),
      content: serializer.fromJson<String>(json['content']),
      modelUsed: serializer.fromJson<String?>(json['modelUsed']),
      tokensUsed: serializer.fromJson<int?>(json['tokensUsed']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      citationsJson: serializer.fromJson<String?>(json['citationsJson']),
      toolCallsJson: serializer.fromJson<String?>(json['toolCallsJson']),
      agentId: serializer.fromJson<String?>(json['agentId']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
      turnId: serializer.fromJson<String?>(json['turnId']),
      turnMetaJson: serializer.fromJson<String?>(json['turnMetaJson']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'conversationId': serializer.toJson<String>(conversationId),
      'role': serializer.toJson<String>(role),
      'content': serializer.toJson<String>(content),
      'modelUsed': serializer.toJson<String?>(modelUsed),
      'tokensUsed': serializer.toJson<int?>(tokensUsed),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'citationsJson': serializer.toJson<String?>(citationsJson),
      'toolCallsJson': serializer.toJson<String?>(toolCallsJson),
      'agentId': serializer.toJson<String?>(agentId),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
      'turnId': serializer.toJson<String?>(turnId),
      'turnMetaJson': serializer.toJson<String?>(turnMetaJson),
    };
  }

  Message copyWith({
    String? id,
    String? conversationId,
    String? role,
    String? content,
    Value<String?> modelUsed = const Value.absent(),
    Value<int?> tokensUsed = const Value.absent(),
    DateTime? createdAt,
    Value<String?> citationsJson = const Value.absent(),
    Value<String?> toolCallsJson = const Value.absent(),
    Value<String?> agentId = const Value.absent(),
    Value<DateTime?> updatedAt = const Value.absent(),
    Value<String?> turnId = const Value.absent(),
    Value<String?> turnMetaJson = const Value.absent(),
  }) => Message(
    id: id ?? this.id,
    conversationId: conversationId ?? this.conversationId,
    role: role ?? this.role,
    content: content ?? this.content,
    modelUsed: modelUsed.present ? modelUsed.value : this.modelUsed,
    tokensUsed: tokensUsed.present ? tokensUsed.value : this.tokensUsed,
    createdAt: createdAt ?? this.createdAt,
    citationsJson: citationsJson.present
        ? citationsJson.value
        : this.citationsJson,
    toolCallsJson: toolCallsJson.present
        ? toolCallsJson.value
        : this.toolCallsJson,
    agentId: agentId.present ? agentId.value : this.agentId,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
    turnId: turnId.present ? turnId.value : this.turnId,
    turnMetaJson: turnMetaJson.present ? turnMetaJson.value : this.turnMetaJson,
  );
  Message copyWithCompanion(MessagesCompanion data) {
    return Message(
      id: data.id.present ? data.id.value : this.id,
      conversationId: data.conversationId.present
          ? data.conversationId.value
          : this.conversationId,
      role: data.role.present ? data.role.value : this.role,
      content: data.content.present ? data.content.value : this.content,
      modelUsed: data.modelUsed.present ? data.modelUsed.value : this.modelUsed,
      tokensUsed: data.tokensUsed.present
          ? data.tokensUsed.value
          : this.tokensUsed,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      citationsJson: data.citationsJson.present
          ? data.citationsJson.value
          : this.citationsJson,
      toolCallsJson: data.toolCallsJson.present
          ? data.toolCallsJson.value
          : this.toolCallsJson,
      agentId: data.agentId.present ? data.agentId.value : this.agentId,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
      turnId: data.turnId.present ? data.turnId.value : this.turnId,
      turnMetaJson: data.turnMetaJson.present
          ? data.turnMetaJson.value
          : this.turnMetaJson,
    );
  }

  @override
  String toString() {
    return (StringBuffer('Message(')
          ..write('id: $id, ')
          ..write('conversationId: $conversationId, ')
          ..write('role: $role, ')
          ..write('content: $content, ')
          ..write('modelUsed: $modelUsed, ')
          ..write('tokensUsed: $tokensUsed, ')
          ..write('createdAt: $createdAt, ')
          ..write('citationsJson: $citationsJson, ')
          ..write('toolCallsJson: $toolCallsJson, ')
          ..write('agentId: $agentId, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('turnId: $turnId, ')
          ..write('turnMetaJson: $turnMetaJson')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    conversationId,
    role,
    content,
    modelUsed,
    tokensUsed,
    createdAt,
    citationsJson,
    toolCallsJson,
    agentId,
    updatedAt,
    turnId,
    turnMetaJson,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is Message &&
          other.id == this.id &&
          other.conversationId == this.conversationId &&
          other.role == this.role &&
          other.content == this.content &&
          other.modelUsed == this.modelUsed &&
          other.tokensUsed == this.tokensUsed &&
          other.createdAt == this.createdAt &&
          other.citationsJson == this.citationsJson &&
          other.toolCallsJson == this.toolCallsJson &&
          other.agentId == this.agentId &&
          other.updatedAt == this.updatedAt &&
          other.turnId == this.turnId &&
          other.turnMetaJson == this.turnMetaJson);
}

class MessagesCompanion extends UpdateCompanion<Message> {
  final Value<String> id;
  final Value<String> conversationId;
  final Value<String> role;
  final Value<String> content;
  final Value<String?> modelUsed;
  final Value<int?> tokensUsed;
  final Value<DateTime> createdAt;
  final Value<String?> citationsJson;
  final Value<String?> toolCallsJson;
  final Value<String?> agentId;
  final Value<DateTime?> updatedAt;
  final Value<String?> turnId;
  final Value<String?> turnMetaJson;
  final Value<int> rowid;
  const MessagesCompanion({
    this.id = const Value.absent(),
    this.conversationId = const Value.absent(),
    this.role = const Value.absent(),
    this.content = const Value.absent(),
    this.modelUsed = const Value.absent(),
    this.tokensUsed = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.citationsJson = const Value.absent(),
    this.toolCallsJson = const Value.absent(),
    this.agentId = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.turnId = const Value.absent(),
    this.turnMetaJson = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  MessagesCompanion.insert({
    required String id,
    required String conversationId,
    required String role,
    required String content,
    this.modelUsed = const Value.absent(),
    this.tokensUsed = const Value.absent(),
    required DateTime createdAt,
    this.citationsJson = const Value.absent(),
    this.toolCallsJson = const Value.absent(),
    this.agentId = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.turnId = const Value.absent(),
    this.turnMetaJson = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       conversationId = Value(conversationId),
       role = Value(role),
       content = Value(content),
       createdAt = Value(createdAt);
  static Insertable<Message> custom({
    Expression<String>? id,
    Expression<String>? conversationId,
    Expression<String>? role,
    Expression<String>? content,
    Expression<String>? modelUsed,
    Expression<int>? tokensUsed,
    Expression<DateTime>? createdAt,
    Expression<String>? citationsJson,
    Expression<String>? toolCallsJson,
    Expression<String>? agentId,
    Expression<DateTime>? updatedAt,
    Expression<String>? turnId,
    Expression<String>? turnMetaJson,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (conversationId != null) 'conversation_id': conversationId,
      if (role != null) 'role': role,
      if (content != null) 'content': content,
      if (modelUsed != null) 'model_used': modelUsed,
      if (tokensUsed != null) 'tokens_used': tokensUsed,
      if (createdAt != null) 'created_at': createdAt,
      if (citationsJson != null) 'citations_json': citationsJson,
      if (toolCallsJson != null) 'tool_calls_json': toolCallsJson,
      if (agentId != null) 'agent_id': agentId,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (turnId != null) 'turn_id': turnId,
      if (turnMetaJson != null) 'turn_meta_json': turnMetaJson,
      if (rowid != null) 'rowid': rowid,
    });
  }

  MessagesCompanion copyWith({
    Value<String>? id,
    Value<String>? conversationId,
    Value<String>? role,
    Value<String>? content,
    Value<String?>? modelUsed,
    Value<int?>? tokensUsed,
    Value<DateTime>? createdAt,
    Value<String?>? citationsJson,
    Value<String?>? toolCallsJson,
    Value<String?>? agentId,
    Value<DateTime?>? updatedAt,
    Value<String?>? turnId,
    Value<String?>? turnMetaJson,
    Value<int>? rowid,
  }) {
    return MessagesCompanion(
      id: id ?? this.id,
      conversationId: conversationId ?? this.conversationId,
      role: role ?? this.role,
      content: content ?? this.content,
      modelUsed: modelUsed ?? this.modelUsed,
      tokensUsed: tokensUsed ?? this.tokensUsed,
      createdAt: createdAt ?? this.createdAt,
      citationsJson: citationsJson ?? this.citationsJson,
      toolCallsJson: toolCallsJson ?? this.toolCallsJson,
      agentId: agentId ?? this.agentId,
      updatedAt: updatedAt ?? this.updatedAt,
      turnId: turnId ?? this.turnId,
      turnMetaJson: turnMetaJson ?? this.turnMetaJson,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (conversationId.present) {
      map['conversation_id'] = Variable<String>(conversationId.value);
    }
    if (role.present) {
      map['role'] = Variable<String>(role.value);
    }
    if (content.present) {
      map['content'] = Variable<String>(
        $MessagesTable.$convertercontent.toSql(content.value),
      );
    }
    if (modelUsed.present) {
      map['model_used'] = Variable<String>(modelUsed.value);
    }
    if (tokensUsed.present) {
      map['tokens_used'] = Variable<int>(tokensUsed.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (citationsJson.present) {
      map['citations_json'] = Variable<String>(
        $MessagesTable.$convertercitationsJsonn.toSql(citationsJson.value),
      );
    }
    if (toolCallsJson.present) {
      map['tool_calls_json'] = Variable<String>(
        $MessagesTable.$convertertoolCallsJsonn.toSql(toolCallsJson.value),
      );
    }
    if (agentId.present) {
      map['agent_id'] = Variable<String>(agentId.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (turnId.present) {
      map['turn_id'] = Variable<String>(turnId.value);
    }
    if (turnMetaJson.present) {
      map['turn_meta_json'] = Variable<String>(
        $MessagesTable.$converterturnMetaJsonn.toSql(turnMetaJson.value),
      );
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('MessagesCompanion(')
          ..write('id: $id, ')
          ..write('conversationId: $conversationId, ')
          ..write('role: $role, ')
          ..write('content: $content, ')
          ..write('modelUsed: $modelUsed, ')
          ..write('tokensUsed: $tokensUsed, ')
          ..write('createdAt: $createdAt, ')
          ..write('citationsJson: $citationsJson, ')
          ..write('toolCallsJson: $toolCallsJson, ')
          ..write('agentId: $agentId, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('turnId: $turnId, ')
          ..write('turnMetaJson: $turnMetaJson, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $AgentConfigurationsTable extends AgentConfigurations
    with TableInfo<$AgentConfigurationsTable, AgentConfiguration> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $AgentConfigurationsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _agentIdMeta = const VerificationMeta(
    'agentId',
  );
  @override
  late final GeneratedColumn<String> agentId = GeneratedColumn<String>(
    'agent_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _systemInstructionMeta = const VerificationMeta(
    'systemInstruction',
  );
  @override
  late final GeneratedColumn<String> systemInstruction =
      GeneratedColumn<String>(
        'system_instruction',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [agentId, systemInstruction, updatedAt];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'agent_configurations';
  @override
  VerificationContext validateIntegrity(
    Insertable<AgentConfiguration> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('agent_id')) {
      context.handle(
        _agentIdMeta,
        agentId.isAcceptableOrUnknown(data['agent_id']!, _agentIdMeta),
      );
    } else if (isInserting) {
      context.missing(_agentIdMeta);
    }
    if (data.containsKey('system_instruction')) {
      context.handle(
        _systemInstructionMeta,
        systemInstruction.isAcceptableOrUnknown(
          data['system_instruction']!,
          _systemInstructionMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_systemInstructionMeta);
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {agentId};
  @override
  AgentConfiguration map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return AgentConfiguration(
      agentId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}agent_id'],
      )!,
      systemInstruction: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}system_instruction'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      )!,
    );
  }

  @override
  $AgentConfigurationsTable createAlias(String alias) {
    return $AgentConfigurationsTable(attachedDatabase, alias);
  }
}

class AgentConfiguration extends DataClass
    implements Insertable<AgentConfiguration> {
  final String agentId;
  final String systemInstruction;
  final DateTime updatedAt;
  const AgentConfiguration({
    required this.agentId,
    required this.systemInstruction,
    required this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['agent_id'] = Variable<String>(agentId);
    map['system_instruction'] = Variable<String>(systemInstruction);
    map['updated_at'] = Variable<DateTime>(updatedAt);
    return map;
  }

  AgentConfigurationsCompanion toCompanion(bool nullToAbsent) {
    return AgentConfigurationsCompanion(
      agentId: Value(agentId),
      systemInstruction: Value(systemInstruction),
      updatedAt: Value(updatedAt),
    );
  }

  factory AgentConfiguration.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return AgentConfiguration(
      agentId: serializer.fromJson<String>(json['agentId']),
      systemInstruction: serializer.fromJson<String>(json['systemInstruction']),
      updatedAt: serializer.fromJson<DateTime>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'agentId': serializer.toJson<String>(agentId),
      'systemInstruction': serializer.toJson<String>(systemInstruction),
      'updatedAt': serializer.toJson<DateTime>(updatedAt),
    };
  }

  AgentConfiguration copyWith({
    String? agentId,
    String? systemInstruction,
    DateTime? updatedAt,
  }) => AgentConfiguration(
    agentId: agentId ?? this.agentId,
    systemInstruction: systemInstruction ?? this.systemInstruction,
    updatedAt: updatedAt ?? this.updatedAt,
  );
  AgentConfiguration copyWithCompanion(AgentConfigurationsCompanion data) {
    return AgentConfiguration(
      agentId: data.agentId.present ? data.agentId.value : this.agentId,
      systemInstruction: data.systemInstruction.present
          ? data.systemInstruction.value
          : this.systemInstruction,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('AgentConfiguration(')
          ..write('agentId: $agentId, ')
          ..write('systemInstruction: $systemInstruction, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(agentId, systemInstruction, updatedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is AgentConfiguration &&
          other.agentId == this.agentId &&
          other.systemInstruction == this.systemInstruction &&
          other.updatedAt == this.updatedAt);
}

class AgentConfigurationsCompanion extends UpdateCompanion<AgentConfiguration> {
  final Value<String> agentId;
  final Value<String> systemInstruction;
  final Value<DateTime> updatedAt;
  final Value<int> rowid;
  const AgentConfigurationsCompanion({
    this.agentId = const Value.absent(),
    this.systemInstruction = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  AgentConfigurationsCompanion.insert({
    required String agentId,
    required String systemInstruction,
    required DateTime updatedAt,
    this.rowid = const Value.absent(),
  }) : agentId = Value(agentId),
       systemInstruction = Value(systemInstruction),
       updatedAt = Value(updatedAt);
  static Insertable<AgentConfiguration> custom({
    Expression<String>? agentId,
    Expression<String>? systemInstruction,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (agentId != null) 'agent_id': agentId,
      if (systemInstruction != null) 'system_instruction': systemInstruction,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  AgentConfigurationsCompanion copyWith({
    Value<String>? agentId,
    Value<String>? systemInstruction,
    Value<DateTime>? updatedAt,
    Value<int>? rowid,
  }) {
    return AgentConfigurationsCompanion(
      agentId: agentId ?? this.agentId,
      systemInstruction: systemInstruction ?? this.systemInstruction,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (agentId.present) {
      map['agent_id'] = Variable<String>(agentId.value);
    }
    if (systemInstruction.present) {
      map['system_instruction'] = Variable<String>(systemInstruction.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('AgentConfigurationsCompanion(')
          ..write('agentId: $agentId, ')
          ..write('systemInstruction: $systemInstruction, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $UserMemoriesTable extends UserMemories
    with TableInfo<$UserMemoriesTable, UserMemory> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $UserMemoriesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> fact =
      GeneratedColumn<String>(
        'fact',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($UserMemoriesTable.$converterfact);
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    fact,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'user_memories';
  @override
  VerificationContext validateIntegrity(
    Insertable<UserMemory> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  UserMemory map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return UserMemory(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      fact: $UserMemoriesTable.$converterfact.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}fact'],
        )!,
      ),
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $UserMemoriesTable createAlias(String alias) {
    return $UserMemoriesTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $converterfact =
      const EncryptedTextConverter();
}

class UserMemory extends DataClass implements Insertable<UserMemory> {
  final String id;
  final String fact;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const UserMemory({
    required this.id,
    required this.fact,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    {
      map['fact'] = Variable<String>(
        $UserMemoriesTable.$converterfact.toSql(fact),
      );
    }
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  UserMemoriesCompanion toCompanion(bool nullToAbsent) {
    return UserMemoriesCompanion(
      id: Value(id),
      fact: Value(fact),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory UserMemory.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return UserMemory(
      id: serializer.fromJson<String>(json['id']),
      fact: serializer.fromJson<String>(json['fact']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'fact': serializer.toJson<String>(fact),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  UserMemory copyWith({
    String? id,
    String? fact,
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => UserMemory(
    id: id ?? this.id,
    fact: fact ?? this.fact,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  UserMemory copyWithCompanion(UserMemoriesCompanion data) {
    return UserMemory(
      id: data.id.present ? data.id.value : this.id,
      fact: data.fact.present ? data.fact.value : this.fact,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('UserMemory(')
          ..write('id: $id, ')
          ..write('fact: $fact, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, fact, createdAt, isSynced, updatedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is UserMemory &&
          other.id == this.id &&
          other.fact == this.fact &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class UserMemoriesCompanion extends UpdateCompanion<UserMemory> {
  final Value<String> id;
  final Value<String> fact;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const UserMemoriesCompanion({
    this.id = const Value.absent(),
    this.fact = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  UserMemoriesCompanion.insert({
    required String id,
    required String fact,
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       fact = Value(fact),
       createdAt = Value(createdAt);
  static Insertable<UserMemory> custom({
    Expression<String>? id,
    Expression<String>? fact,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (fact != null) 'fact': fact,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  UserMemoriesCompanion copyWith({
    Value<String>? id,
    Value<String>? fact,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return UserMemoriesCompanion(
      id: id ?? this.id,
      fact: fact ?? this.fact,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (fact.present) {
      map['fact'] = Variable<String>(
        $UserMemoriesTable.$converterfact.toSql(fact.value),
      );
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('UserMemoriesCompanion(')
          ..write('id: $id, ')
          ..write('fact: $fact, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $ProjectsTable extends Projects with TableInfo<$ProjectsTable, Project> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $ProjectsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> title =
      GeneratedColumn<String>(
        'title',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($ProjectsTable.$convertertitle);
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> description =
      GeneratedColumn<String>(
        'description',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($ProjectsTable.$converterdescriptionn);
  static const VerificationMeta _statusMeta = const VerificationMeta('status');
  @override
  late final GeneratedColumn<String> status = GeneratedColumn<String>(
    'status',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _targetDateMeta = const VerificationMeta(
    'targetDate',
  );
  @override
  late final GeneratedColumn<DateTime> targetDate = GeneratedColumn<DateTime>(
    'target_date',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    title,
    description,
    status,
    targetDate,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'projects';
  @override
  VerificationContext validateIntegrity(
    Insertable<Project> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('status')) {
      context.handle(
        _statusMeta,
        status.isAcceptableOrUnknown(data['status']!, _statusMeta),
      );
    } else if (isInserting) {
      context.missing(_statusMeta);
    }
    if (data.containsKey('target_date')) {
      context.handle(
        _targetDateMeta,
        targetDate.isAcceptableOrUnknown(data['target_date']!, _targetDateMeta),
      );
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  Project map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return Project(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      title: $ProjectsTable.$convertertitle.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}title'],
        )!,
      ),
      description: $ProjectsTable.$converterdescriptionn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}description'],
        ),
      ),
      status: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}status'],
      )!,
      targetDate: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}target_date'],
      ),
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $ProjectsTable createAlias(String alias) {
    return $ProjectsTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $convertertitle =
      const EncryptedTextConverter();
  static TypeConverter<String, String> $converterdescription =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $converterdescriptionn =
      NullAwareTypeConverter.wrap($converterdescription);
}

class Project extends DataClass implements Insertable<Project> {
  final String id;
  final String title;
  final String? description;
  final String status;
  final DateTime? targetDate;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const Project({
    required this.id,
    required this.title,
    this.description,
    required this.status,
    this.targetDate,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    {
      map['title'] = Variable<String>(
        $ProjectsTable.$convertertitle.toSql(title),
      );
    }
    if (!nullToAbsent || description != null) {
      map['description'] = Variable<String>(
        $ProjectsTable.$converterdescriptionn.toSql(description),
      );
    }
    map['status'] = Variable<String>(status);
    if (!nullToAbsent || targetDate != null) {
      map['target_date'] = Variable<DateTime>(targetDate);
    }
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  ProjectsCompanion toCompanion(bool nullToAbsent) {
    return ProjectsCompanion(
      id: Value(id),
      title: Value(title),
      description: description == null && nullToAbsent
          ? const Value.absent()
          : Value(description),
      status: Value(status),
      targetDate: targetDate == null && nullToAbsent
          ? const Value.absent()
          : Value(targetDate),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory Project.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return Project(
      id: serializer.fromJson<String>(json['id']),
      title: serializer.fromJson<String>(json['title']),
      description: serializer.fromJson<String?>(json['description']),
      status: serializer.fromJson<String>(json['status']),
      targetDate: serializer.fromJson<DateTime?>(json['targetDate']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'title': serializer.toJson<String>(title),
      'description': serializer.toJson<String?>(description),
      'status': serializer.toJson<String>(status),
      'targetDate': serializer.toJson<DateTime?>(targetDate),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  Project copyWith({
    String? id,
    String? title,
    Value<String?> description = const Value.absent(),
    String? status,
    Value<DateTime?> targetDate = const Value.absent(),
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => Project(
    id: id ?? this.id,
    title: title ?? this.title,
    description: description.present ? description.value : this.description,
    status: status ?? this.status,
    targetDate: targetDate.present ? targetDate.value : this.targetDate,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  Project copyWithCompanion(ProjectsCompanion data) {
    return Project(
      id: data.id.present ? data.id.value : this.id,
      title: data.title.present ? data.title.value : this.title,
      description: data.description.present
          ? data.description.value
          : this.description,
      status: data.status.present ? data.status.value : this.status,
      targetDate: data.targetDate.present
          ? data.targetDate.value
          : this.targetDate,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('Project(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('status: $status, ')
          ..write('targetDate: $targetDate, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    title,
    description,
    status,
    targetDate,
    createdAt,
    isSynced,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is Project &&
          other.id == this.id &&
          other.title == this.title &&
          other.description == this.description &&
          other.status == this.status &&
          other.targetDate == this.targetDate &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class ProjectsCompanion extends UpdateCompanion<Project> {
  final Value<String> id;
  final Value<String> title;
  final Value<String?> description;
  final Value<String> status;
  final Value<DateTime?> targetDate;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const ProjectsCompanion({
    this.id = const Value.absent(),
    this.title = const Value.absent(),
    this.description = const Value.absent(),
    this.status = const Value.absent(),
    this.targetDate = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  ProjectsCompanion.insert({
    required String id,
    required String title,
    this.description = const Value.absent(),
    required String status,
    this.targetDate = const Value.absent(),
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       title = Value(title),
       status = Value(status),
       createdAt = Value(createdAt);
  static Insertable<Project> custom({
    Expression<String>? id,
    Expression<String>? title,
    Expression<String>? description,
    Expression<String>? status,
    Expression<DateTime>? targetDate,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (title != null) 'title': title,
      if (description != null) 'description': description,
      if (status != null) 'status': status,
      if (targetDate != null) 'target_date': targetDate,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  ProjectsCompanion copyWith({
    Value<String>? id,
    Value<String>? title,
    Value<String?>? description,
    Value<String>? status,
    Value<DateTime?>? targetDate,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return ProjectsCompanion(
      id: id ?? this.id,
      title: title ?? this.title,
      description: description ?? this.description,
      status: status ?? this.status,
      targetDate: targetDate ?? this.targetDate,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (title.present) {
      map['title'] = Variable<String>(
        $ProjectsTable.$convertertitle.toSql(title.value),
      );
    }
    if (description.present) {
      map['description'] = Variable<String>(
        $ProjectsTable.$converterdescriptionn.toSql(description.value),
      );
    }
    if (status.present) {
      map['status'] = Variable<String>(status.value);
    }
    if (targetDate.present) {
      map['target_date'] = Variable<DateTime>(targetDate.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('ProjectsCompanion(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('status: $status, ')
          ..write('targetDate: $targetDate, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $GoalsTable extends Goals with TableInfo<$GoalsTable, Goal> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $GoalsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> title =
      GeneratedColumn<String>(
        'title',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($GoalsTable.$convertertitle);
  @override
  late final GeneratedColumnWithTypeConverter<String?, String> description =
      GeneratedColumn<String>(
        'description',
        aliasedName,
        true,
        type: DriftSqlType.string,
        requiredDuringInsert: false,
      ).withConverter<String?>($GoalsTable.$converterdescriptionn);
  static const VerificationMeta _projectIdMeta = const VerificationMeta(
    'projectId',
  );
  @override
  late final GeneratedColumn<String> projectId = GeneratedColumn<String>(
    'project_id',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _categoryMeta = const VerificationMeta(
    'category',
  );
  @override
  late final GeneratedColumn<String> category = GeneratedColumn<String>(
    'category',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _statusMeta = const VerificationMeta('status');
  @override
  late final GeneratedColumn<String> status = GeneratedColumn<String>(
    'status',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _priorityMeta = const VerificationMeta(
    'priority',
  );
  @override
  late final GeneratedColumn<String> priority = GeneratedColumn<String>(
    'priority',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _targetDateMeta = const VerificationMeta(
    'targetDate',
  );
  @override
  late final GeneratedColumn<DateTime> targetDate = GeneratedColumn<DateTime>(
    'target_date',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _progressPctMeta = const VerificationMeta(
    'progressPct',
  );
  @override
  late final GeneratedColumn<int> progressPct = GeneratedColumn<int>(
    'progress_pct',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isSyncedMeta = const VerificationMeta(
    'isSynced',
  );
  @override
  late final GeneratedColumn<bool> isSynced = GeneratedColumn<bool>(
    'is_synced',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_synced" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    true,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    title,
    description,
    projectId,
    category,
    status,
    priority,
    targetDate,
    progressPct,
    createdAt,
    isSynced,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'goals';
  @override
  VerificationContext validateIntegrity(
    Insertable<Goal> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('project_id')) {
      context.handle(
        _projectIdMeta,
        projectId.isAcceptableOrUnknown(data['project_id']!, _projectIdMeta),
      );
    }
    if (data.containsKey('category')) {
      context.handle(
        _categoryMeta,
        category.isAcceptableOrUnknown(data['category']!, _categoryMeta),
      );
    }
    if (data.containsKey('status')) {
      context.handle(
        _statusMeta,
        status.isAcceptableOrUnknown(data['status']!, _statusMeta),
      );
    } else if (isInserting) {
      context.missing(_statusMeta);
    }
    if (data.containsKey('priority')) {
      context.handle(
        _priorityMeta,
        priority.isAcceptableOrUnknown(data['priority']!, _priorityMeta),
      );
    } else if (isInserting) {
      context.missing(_priorityMeta);
    }
    if (data.containsKey('target_date')) {
      context.handle(
        _targetDateMeta,
        targetDate.isAcceptableOrUnknown(data['target_date']!, _targetDateMeta),
      );
    }
    if (data.containsKey('progress_pct')) {
      context.handle(
        _progressPctMeta,
        progressPct.isAcceptableOrUnknown(
          data['progress_pct']!,
          _progressPctMeta,
        ),
      );
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('is_synced')) {
      context.handle(
        _isSyncedMeta,
        isSynced.isAcceptableOrUnknown(data['is_synced']!, _isSyncedMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  Goal map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return Goal(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      title: $GoalsTable.$convertertitle.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}title'],
        )!,
      ),
      description: $GoalsTable.$converterdescriptionn.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}description'],
        ),
      ),
      projectId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}project_id'],
      ),
      category: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}category'],
      ),
      status: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}status'],
      )!,
      priority: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}priority'],
      )!,
      targetDate: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}target_date'],
      ),
      progressPct: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}progress_pct'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      isSynced: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_synced'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      ),
    );
  }

  @override
  $GoalsTable createAlias(String alias) {
    return $GoalsTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $convertertitle =
      const EncryptedTextConverter();
  static TypeConverter<String, String> $converterdescription =
      const EncryptedTextConverter();
  static TypeConverter<String?, String?> $converterdescriptionn =
      NullAwareTypeConverter.wrap($converterdescription);
}

class Goal extends DataClass implements Insertable<Goal> {
  final String id;
  final String title;
  final String? description;
  final String? projectId;
  final String? category;
  final String status;
  final String priority;
  final DateTime? targetDate;
  final int progressPct;
  final DateTime createdAt;
  final bool isSynced;
  final DateTime? updatedAt;
  const Goal({
    required this.id,
    required this.title,
    this.description,
    this.projectId,
    this.category,
    required this.status,
    required this.priority,
    this.targetDate,
    required this.progressPct,
    required this.createdAt,
    required this.isSynced,
    this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    {
      map['title'] = Variable<String>($GoalsTable.$convertertitle.toSql(title));
    }
    if (!nullToAbsent || description != null) {
      map['description'] = Variable<String>(
        $GoalsTable.$converterdescriptionn.toSql(description),
      );
    }
    if (!nullToAbsent || projectId != null) {
      map['project_id'] = Variable<String>(projectId);
    }
    if (!nullToAbsent || category != null) {
      map['category'] = Variable<String>(category);
    }
    map['status'] = Variable<String>(status);
    map['priority'] = Variable<String>(priority);
    if (!nullToAbsent || targetDate != null) {
      map['target_date'] = Variable<DateTime>(targetDate);
    }
    map['progress_pct'] = Variable<int>(progressPct);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['is_synced'] = Variable<bool>(isSynced);
    if (!nullToAbsent || updatedAt != null) {
      map['updated_at'] = Variable<DateTime>(updatedAt);
    }
    return map;
  }

  GoalsCompanion toCompanion(bool nullToAbsent) {
    return GoalsCompanion(
      id: Value(id),
      title: Value(title),
      description: description == null && nullToAbsent
          ? const Value.absent()
          : Value(description),
      projectId: projectId == null && nullToAbsent
          ? const Value.absent()
          : Value(projectId),
      category: category == null && nullToAbsent
          ? const Value.absent()
          : Value(category),
      status: Value(status),
      priority: Value(priority),
      targetDate: targetDate == null && nullToAbsent
          ? const Value.absent()
          : Value(targetDate),
      progressPct: Value(progressPct),
      createdAt: Value(createdAt),
      isSynced: Value(isSynced),
      updatedAt: updatedAt == null && nullToAbsent
          ? const Value.absent()
          : Value(updatedAt),
    );
  }

  factory Goal.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return Goal(
      id: serializer.fromJson<String>(json['id']),
      title: serializer.fromJson<String>(json['title']),
      description: serializer.fromJson<String?>(json['description']),
      projectId: serializer.fromJson<String?>(json['projectId']),
      category: serializer.fromJson<String?>(json['category']),
      status: serializer.fromJson<String>(json['status']),
      priority: serializer.fromJson<String>(json['priority']),
      targetDate: serializer.fromJson<DateTime?>(json['targetDate']),
      progressPct: serializer.fromJson<int>(json['progressPct']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      isSynced: serializer.fromJson<bool>(json['isSynced']),
      updatedAt: serializer.fromJson<DateTime?>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'title': serializer.toJson<String>(title),
      'description': serializer.toJson<String?>(description),
      'projectId': serializer.toJson<String?>(projectId),
      'category': serializer.toJson<String?>(category),
      'status': serializer.toJson<String>(status),
      'priority': serializer.toJson<String>(priority),
      'targetDate': serializer.toJson<DateTime?>(targetDate),
      'progressPct': serializer.toJson<int>(progressPct),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'isSynced': serializer.toJson<bool>(isSynced),
      'updatedAt': serializer.toJson<DateTime?>(updatedAt),
    };
  }

  Goal copyWith({
    String? id,
    String? title,
    Value<String?> description = const Value.absent(),
    Value<String?> projectId = const Value.absent(),
    Value<String?> category = const Value.absent(),
    String? status,
    String? priority,
    Value<DateTime?> targetDate = const Value.absent(),
    int? progressPct,
    DateTime? createdAt,
    bool? isSynced,
    Value<DateTime?> updatedAt = const Value.absent(),
  }) => Goal(
    id: id ?? this.id,
    title: title ?? this.title,
    description: description.present ? description.value : this.description,
    projectId: projectId.present ? projectId.value : this.projectId,
    category: category.present ? category.value : this.category,
    status: status ?? this.status,
    priority: priority ?? this.priority,
    targetDate: targetDate.present ? targetDate.value : this.targetDate,
    progressPct: progressPct ?? this.progressPct,
    createdAt: createdAt ?? this.createdAt,
    isSynced: isSynced ?? this.isSynced,
    updatedAt: updatedAt.present ? updatedAt.value : this.updatedAt,
  );
  Goal copyWithCompanion(GoalsCompanion data) {
    return Goal(
      id: data.id.present ? data.id.value : this.id,
      title: data.title.present ? data.title.value : this.title,
      description: data.description.present
          ? data.description.value
          : this.description,
      projectId: data.projectId.present ? data.projectId.value : this.projectId,
      category: data.category.present ? data.category.value : this.category,
      status: data.status.present ? data.status.value : this.status,
      priority: data.priority.present ? data.priority.value : this.priority,
      targetDate: data.targetDate.present
          ? data.targetDate.value
          : this.targetDate,
      progressPct: data.progressPct.present
          ? data.progressPct.value
          : this.progressPct,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      isSynced: data.isSynced.present ? data.isSynced.value : this.isSynced,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('Goal(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('projectId: $projectId, ')
          ..write('category: $category, ')
          ..write('status: $status, ')
          ..write('priority: $priority, ')
          ..write('targetDate: $targetDate, ')
          ..write('progressPct: $progressPct, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    title,
    description,
    projectId,
    category,
    status,
    priority,
    targetDate,
    progressPct,
    createdAt,
    isSynced,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is Goal &&
          other.id == this.id &&
          other.title == this.title &&
          other.description == this.description &&
          other.projectId == this.projectId &&
          other.category == this.category &&
          other.status == this.status &&
          other.priority == this.priority &&
          other.targetDate == this.targetDate &&
          other.progressPct == this.progressPct &&
          other.createdAt == this.createdAt &&
          other.isSynced == this.isSynced &&
          other.updatedAt == this.updatedAt);
}

class GoalsCompanion extends UpdateCompanion<Goal> {
  final Value<String> id;
  final Value<String> title;
  final Value<String?> description;
  final Value<String?> projectId;
  final Value<String?> category;
  final Value<String> status;
  final Value<String> priority;
  final Value<DateTime?> targetDate;
  final Value<int> progressPct;
  final Value<DateTime> createdAt;
  final Value<bool> isSynced;
  final Value<DateTime?> updatedAt;
  final Value<int> rowid;
  const GoalsCompanion({
    this.id = const Value.absent(),
    this.title = const Value.absent(),
    this.description = const Value.absent(),
    this.projectId = const Value.absent(),
    this.category = const Value.absent(),
    this.status = const Value.absent(),
    this.priority = const Value.absent(),
    this.targetDate = const Value.absent(),
    this.progressPct = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  GoalsCompanion.insert({
    required String id,
    required String title,
    this.description = const Value.absent(),
    this.projectId = const Value.absent(),
    this.category = const Value.absent(),
    required String status,
    required String priority,
    this.targetDate = const Value.absent(),
    this.progressPct = const Value.absent(),
    required DateTime createdAt,
    this.isSynced = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       title = Value(title),
       status = Value(status),
       priority = Value(priority),
       createdAt = Value(createdAt);
  static Insertable<Goal> custom({
    Expression<String>? id,
    Expression<String>? title,
    Expression<String>? description,
    Expression<String>? projectId,
    Expression<String>? category,
    Expression<String>? status,
    Expression<String>? priority,
    Expression<DateTime>? targetDate,
    Expression<int>? progressPct,
    Expression<DateTime>? createdAt,
    Expression<bool>? isSynced,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (title != null) 'title': title,
      if (description != null) 'description': description,
      if (projectId != null) 'project_id': projectId,
      if (category != null) 'category': category,
      if (status != null) 'status': status,
      if (priority != null) 'priority': priority,
      if (targetDate != null) 'target_date': targetDate,
      if (progressPct != null) 'progress_pct': progressPct,
      if (createdAt != null) 'created_at': createdAt,
      if (isSynced != null) 'is_synced': isSynced,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  GoalsCompanion copyWith({
    Value<String>? id,
    Value<String>? title,
    Value<String?>? description,
    Value<String?>? projectId,
    Value<String?>? category,
    Value<String>? status,
    Value<String>? priority,
    Value<DateTime?>? targetDate,
    Value<int>? progressPct,
    Value<DateTime>? createdAt,
    Value<bool>? isSynced,
    Value<DateTime?>? updatedAt,
    Value<int>? rowid,
  }) {
    return GoalsCompanion(
      id: id ?? this.id,
      title: title ?? this.title,
      description: description ?? this.description,
      projectId: projectId ?? this.projectId,
      category: category ?? this.category,
      status: status ?? this.status,
      priority: priority ?? this.priority,
      targetDate: targetDate ?? this.targetDate,
      progressPct: progressPct ?? this.progressPct,
      createdAt: createdAt ?? this.createdAt,
      isSynced: isSynced ?? this.isSynced,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (title.present) {
      map['title'] = Variable<String>(
        $GoalsTable.$convertertitle.toSql(title.value),
      );
    }
    if (description.present) {
      map['description'] = Variable<String>(
        $GoalsTable.$converterdescriptionn.toSql(description.value),
      );
    }
    if (projectId.present) {
      map['project_id'] = Variable<String>(projectId.value);
    }
    if (category.present) {
      map['category'] = Variable<String>(category.value);
    }
    if (status.present) {
      map['status'] = Variable<String>(status.value);
    }
    if (priority.present) {
      map['priority'] = Variable<String>(priority.value);
    }
    if (targetDate.present) {
      map['target_date'] = Variable<DateTime>(targetDate.value);
    }
    if (progressPct.present) {
      map['progress_pct'] = Variable<int>(progressPct.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (isSynced.present) {
      map['is_synced'] = Variable<bool>(isSynced.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('GoalsCompanion(')
          ..write('id: $id, ')
          ..write('title: $title, ')
          ..write('description: $description, ')
          ..write('projectId: $projectId, ')
          ..write('category: $category, ')
          ..write('status: $status, ')
          ..write('priority: $priority, ')
          ..write('targetDate: $targetDate, ')
          ..write('progressPct: $progressPct, ')
          ..write('createdAt: $createdAt, ')
          ..write('isSynced: $isSynced, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $PendingDeletesTable extends PendingDeletes
    with TableInfo<$PendingDeletesTable, PendingDelete> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $PendingDeletesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _recordIdMeta = const VerificationMeta(
    'recordId',
  );
  @override
  late final GeneratedColumn<String> recordId = GeneratedColumn<String>(
    'record_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _targetTableMeta = const VerificationMeta(
    'targetTable',
  );
  @override
  late final GeneratedColumn<String> targetTable = GeneratedColumn<String>(
    'target_table',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _deletedAtMeta = const VerificationMeta(
    'deletedAt',
  );
  @override
  late final GeneratedColumn<DateTime> deletedAt = GeneratedColumn<DateTime>(
    'deleted_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [recordId, targetTable, deletedAt];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'pending_deletes';
  @override
  VerificationContext validateIntegrity(
    Insertable<PendingDelete> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('record_id')) {
      context.handle(
        _recordIdMeta,
        recordId.isAcceptableOrUnknown(data['record_id']!, _recordIdMeta),
      );
    } else if (isInserting) {
      context.missing(_recordIdMeta);
    }
    if (data.containsKey('target_table')) {
      context.handle(
        _targetTableMeta,
        targetTable.isAcceptableOrUnknown(
          data['target_table']!,
          _targetTableMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_targetTableMeta);
    }
    if (data.containsKey('deleted_at')) {
      context.handle(
        _deletedAtMeta,
        deletedAt.isAcceptableOrUnknown(data['deleted_at']!, _deletedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_deletedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {recordId, targetTable};
  @override
  PendingDelete map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return PendingDelete(
      recordId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}record_id'],
      )!,
      targetTable: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}target_table'],
      )!,
      deletedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}deleted_at'],
      )!,
    );
  }

  @override
  $PendingDeletesTable createAlias(String alias) {
    return $PendingDeletesTable(attachedDatabase, alias);
  }
}

class PendingDelete extends DataClass implements Insertable<PendingDelete> {
  final String recordId;
  final String targetTable;
  final DateTime deletedAt;
  const PendingDelete({
    required this.recordId,
    required this.targetTable,
    required this.deletedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['record_id'] = Variable<String>(recordId);
    map['target_table'] = Variable<String>(targetTable);
    map['deleted_at'] = Variable<DateTime>(deletedAt);
    return map;
  }

  PendingDeletesCompanion toCompanion(bool nullToAbsent) {
    return PendingDeletesCompanion(
      recordId: Value(recordId),
      targetTable: Value(targetTable),
      deletedAt: Value(deletedAt),
    );
  }

  factory PendingDelete.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return PendingDelete(
      recordId: serializer.fromJson<String>(json['recordId']),
      targetTable: serializer.fromJson<String>(json['targetTable']),
      deletedAt: serializer.fromJson<DateTime>(json['deletedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'recordId': serializer.toJson<String>(recordId),
      'targetTable': serializer.toJson<String>(targetTable),
      'deletedAt': serializer.toJson<DateTime>(deletedAt),
    };
  }

  PendingDelete copyWith({
    String? recordId,
    String? targetTable,
    DateTime? deletedAt,
  }) => PendingDelete(
    recordId: recordId ?? this.recordId,
    targetTable: targetTable ?? this.targetTable,
    deletedAt: deletedAt ?? this.deletedAt,
  );
  PendingDelete copyWithCompanion(PendingDeletesCompanion data) {
    return PendingDelete(
      recordId: data.recordId.present ? data.recordId.value : this.recordId,
      targetTable: data.targetTable.present
          ? data.targetTable.value
          : this.targetTable,
      deletedAt: data.deletedAt.present ? data.deletedAt.value : this.deletedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('PendingDelete(')
          ..write('recordId: $recordId, ')
          ..write('targetTable: $targetTable, ')
          ..write('deletedAt: $deletedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(recordId, targetTable, deletedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is PendingDelete &&
          other.recordId == this.recordId &&
          other.targetTable == this.targetTable &&
          other.deletedAt == this.deletedAt);
}

class PendingDeletesCompanion extends UpdateCompanion<PendingDelete> {
  final Value<String> recordId;
  final Value<String> targetTable;
  final Value<DateTime> deletedAt;
  final Value<int> rowid;
  const PendingDeletesCompanion({
    this.recordId = const Value.absent(),
    this.targetTable = const Value.absent(),
    this.deletedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  PendingDeletesCompanion.insert({
    required String recordId,
    required String targetTable,
    required DateTime deletedAt,
    this.rowid = const Value.absent(),
  }) : recordId = Value(recordId),
       targetTable = Value(targetTable),
       deletedAt = Value(deletedAt);
  static Insertable<PendingDelete> custom({
    Expression<String>? recordId,
    Expression<String>? targetTable,
    Expression<DateTime>? deletedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (recordId != null) 'record_id': recordId,
      if (targetTable != null) 'target_table': targetTable,
      if (deletedAt != null) 'deleted_at': deletedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  PendingDeletesCompanion copyWith({
    Value<String>? recordId,
    Value<String>? targetTable,
    Value<DateTime>? deletedAt,
    Value<int>? rowid,
  }) {
    return PendingDeletesCompanion(
      recordId: recordId ?? this.recordId,
      targetTable: targetTable ?? this.targetTable,
      deletedAt: deletedAt ?? this.deletedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (recordId.present) {
      map['record_id'] = Variable<String>(recordId.value);
    }
    if (targetTable.present) {
      map['target_table'] = Variable<String>(targetTable.value);
    }
    if (deletedAt.present) {
      map['deleted_at'] = Variable<DateTime>(deletedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('PendingDeletesCompanion(')
          ..write('recordId: $recordId, ')
          ..write('targetTable: $targetTable, ')
          ..write('deletedAt: $deletedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $LearningLogsTable extends LearningLogs
    with TableInfo<$LearningLogsTable, LearningLog> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $LearningLogsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  late final GeneratedColumnWithTypeConverter<String, String> prompt =
      GeneratedColumn<String>(
        'prompt',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($LearningLogsTable.$converterprompt);
  @override
  late final GeneratedColumnWithTypeConverter<String, String> response =
      GeneratedColumn<String>(
        'response',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      ).withConverter<String>($LearningLogsTable.$converterresponse);
  static const VerificationMeta _ratingMeta = const VerificationMeta('rating');
  @override
  late final GeneratedColumn<int> rating = GeneratedColumn<int>(
    'rating',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _correctionMeta = const VerificationMeta(
    'correction',
  );
  @override
  late final GeneratedColumn<String> correction = GeneratedColumn<String>(
    'correction',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _modelUsedMeta = const VerificationMeta(
    'modelUsed',
  );
  @override
  late final GeneratedColumn<String> modelUsed = GeneratedColumn<String>(
    'model_used',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    prompt,
    response,
    rating,
    correction,
    modelUsed,
    createdAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'learning_logs';
  @override
  VerificationContext validateIntegrity(
    Insertable<LearningLog> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('rating')) {
      context.handle(
        _ratingMeta,
        rating.isAcceptableOrUnknown(data['rating']!, _ratingMeta),
      );
    } else if (isInserting) {
      context.missing(_ratingMeta);
    }
    if (data.containsKey('correction')) {
      context.handle(
        _correctionMeta,
        correction.isAcceptableOrUnknown(data['correction']!, _correctionMeta),
      );
    }
    if (data.containsKey('model_used')) {
      context.handle(
        _modelUsedMeta,
        modelUsed.isAcceptableOrUnknown(data['model_used']!, _modelUsedMeta),
      );
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  LearningLog map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return LearningLog(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      prompt: $LearningLogsTable.$converterprompt.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}prompt'],
        )!,
      ),
      response: $LearningLogsTable.$converterresponse.fromSql(
        attachedDatabase.typeMapping.read(
          DriftSqlType.string,
          data['${effectivePrefix}response'],
        )!,
      ),
      rating: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}rating'],
      )!,
      correction: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}correction'],
      ),
      modelUsed: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}model_used'],
      ),
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
    );
  }

  @override
  $LearningLogsTable createAlias(String alias) {
    return $LearningLogsTable(attachedDatabase, alias);
  }

  static TypeConverter<String, String> $converterprompt =
      const EncryptedTextConverter();
  static TypeConverter<String, String> $converterresponse =
      const EncryptedTextConverter();
}

class LearningLog extends DataClass implements Insertable<LearningLog> {
  final String id;
  final String prompt;
  final String response;
  final int rating;
  final String? correction;
  final String? modelUsed;
  final DateTime createdAt;
  const LearningLog({
    required this.id,
    required this.prompt,
    required this.response,
    required this.rating,
    this.correction,
    this.modelUsed,
    required this.createdAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    {
      map['prompt'] = Variable<String>(
        $LearningLogsTable.$converterprompt.toSql(prompt),
      );
    }
    {
      map['response'] = Variable<String>(
        $LearningLogsTable.$converterresponse.toSql(response),
      );
    }
    map['rating'] = Variable<int>(rating);
    if (!nullToAbsent || correction != null) {
      map['correction'] = Variable<String>(correction);
    }
    if (!nullToAbsent || modelUsed != null) {
      map['model_used'] = Variable<String>(modelUsed);
    }
    map['created_at'] = Variable<DateTime>(createdAt);
    return map;
  }

  LearningLogsCompanion toCompanion(bool nullToAbsent) {
    return LearningLogsCompanion(
      id: Value(id),
      prompt: Value(prompt),
      response: Value(response),
      rating: Value(rating),
      correction: correction == null && nullToAbsent
          ? const Value.absent()
          : Value(correction),
      modelUsed: modelUsed == null && nullToAbsent
          ? const Value.absent()
          : Value(modelUsed),
      createdAt: Value(createdAt),
    );
  }

  factory LearningLog.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return LearningLog(
      id: serializer.fromJson<String>(json['id']),
      prompt: serializer.fromJson<String>(json['prompt']),
      response: serializer.fromJson<String>(json['response']),
      rating: serializer.fromJson<int>(json['rating']),
      correction: serializer.fromJson<String?>(json['correction']),
      modelUsed: serializer.fromJson<String?>(json['modelUsed']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'prompt': serializer.toJson<String>(prompt),
      'response': serializer.toJson<String>(response),
      'rating': serializer.toJson<int>(rating),
      'correction': serializer.toJson<String?>(correction),
      'modelUsed': serializer.toJson<String?>(modelUsed),
      'createdAt': serializer.toJson<DateTime>(createdAt),
    };
  }

  LearningLog copyWith({
    String? id,
    String? prompt,
    String? response,
    int? rating,
    Value<String?> correction = const Value.absent(),
    Value<String?> modelUsed = const Value.absent(),
    DateTime? createdAt,
  }) => LearningLog(
    id: id ?? this.id,
    prompt: prompt ?? this.prompt,
    response: response ?? this.response,
    rating: rating ?? this.rating,
    correction: correction.present ? correction.value : this.correction,
    modelUsed: modelUsed.present ? modelUsed.value : this.modelUsed,
    createdAt: createdAt ?? this.createdAt,
  );
  LearningLog copyWithCompanion(LearningLogsCompanion data) {
    return LearningLog(
      id: data.id.present ? data.id.value : this.id,
      prompt: data.prompt.present ? data.prompt.value : this.prompt,
      response: data.response.present ? data.response.value : this.response,
      rating: data.rating.present ? data.rating.value : this.rating,
      correction: data.correction.present
          ? data.correction.value
          : this.correction,
      modelUsed: data.modelUsed.present ? data.modelUsed.value : this.modelUsed,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('LearningLog(')
          ..write('id: $id, ')
          ..write('prompt: $prompt, ')
          ..write('response: $response, ')
          ..write('rating: $rating, ')
          ..write('correction: $correction, ')
          ..write('modelUsed: $modelUsed, ')
          ..write('createdAt: $createdAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    prompt,
    response,
    rating,
    correction,
    modelUsed,
    createdAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is LearningLog &&
          other.id == this.id &&
          other.prompt == this.prompt &&
          other.response == this.response &&
          other.rating == this.rating &&
          other.correction == this.correction &&
          other.modelUsed == this.modelUsed &&
          other.createdAt == this.createdAt);
}

class LearningLogsCompanion extends UpdateCompanion<LearningLog> {
  final Value<String> id;
  final Value<String> prompt;
  final Value<String> response;
  final Value<int> rating;
  final Value<String?> correction;
  final Value<String?> modelUsed;
  final Value<DateTime> createdAt;
  final Value<int> rowid;
  const LearningLogsCompanion({
    this.id = const Value.absent(),
    this.prompt = const Value.absent(),
    this.response = const Value.absent(),
    this.rating = const Value.absent(),
    this.correction = const Value.absent(),
    this.modelUsed = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  LearningLogsCompanion.insert({
    required String id,
    required String prompt,
    required String response,
    required int rating,
    this.correction = const Value.absent(),
    this.modelUsed = const Value.absent(),
    required DateTime createdAt,
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       prompt = Value(prompt),
       response = Value(response),
       rating = Value(rating),
       createdAt = Value(createdAt);
  static Insertable<LearningLog> custom({
    Expression<String>? id,
    Expression<String>? prompt,
    Expression<String>? response,
    Expression<int>? rating,
    Expression<String>? correction,
    Expression<String>? modelUsed,
    Expression<DateTime>? createdAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (prompt != null) 'prompt': prompt,
      if (response != null) 'response': response,
      if (rating != null) 'rating': rating,
      if (correction != null) 'correction': correction,
      if (modelUsed != null) 'model_used': modelUsed,
      if (createdAt != null) 'created_at': createdAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  LearningLogsCompanion copyWith({
    Value<String>? id,
    Value<String>? prompt,
    Value<String>? response,
    Value<int>? rating,
    Value<String?>? correction,
    Value<String?>? modelUsed,
    Value<DateTime>? createdAt,
    Value<int>? rowid,
  }) {
    return LearningLogsCompanion(
      id: id ?? this.id,
      prompt: prompt ?? this.prompt,
      response: response ?? this.response,
      rating: rating ?? this.rating,
      correction: correction ?? this.correction,
      modelUsed: modelUsed ?? this.modelUsed,
      createdAt: createdAt ?? this.createdAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (prompt.present) {
      map['prompt'] = Variable<String>(
        $LearningLogsTable.$converterprompt.toSql(prompt.value),
      );
    }
    if (response.present) {
      map['response'] = Variable<String>(
        $LearningLogsTable.$converterresponse.toSql(response.value),
      );
    }
    if (rating.present) {
      map['rating'] = Variable<int>(rating.value);
    }
    if (correction.present) {
      map['correction'] = Variable<String>(correction.value);
    }
    if (modelUsed.present) {
      map['model_used'] = Variable<String>(modelUsed.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('LearningLogsCompanion(')
          ..write('id: $id, ')
          ..write('prompt: $prompt, ')
          ..write('response: $response, ')
          ..write('rating: $rating, ')
          ..write('correction: $correction, ')
          ..write('modelUsed: $modelUsed, ')
          ..write('createdAt: $createdAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $OfflineModelWeightsTable extends OfflineModelWeights
    with TableInfo<$OfflineModelWeightsTable, OfflineModelWeight> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $OfflineModelWeightsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _modelNameMeta = const VerificationMeta(
    'modelName',
  );
  @override
  late final GeneratedColumn<String> modelName = GeneratedColumn<String>(
    'model_name',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _sizeBytesMeta = const VerificationMeta(
    'sizeBytes',
  );
  @override
  late final GeneratedColumn<String> sizeBytes = GeneratedColumn<String>(
    'size_bytes',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _localPathMeta = const VerificationMeta(
    'localPath',
  );
  @override
  late final GeneratedColumn<String> localPath = GeneratedColumn<String>(
    'local_path',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _isDownloadedMeta = const VerificationMeta(
    'isDownloaded',
  );
  @override
  late final GeneratedColumn<bool> isDownloaded = GeneratedColumn<bool>(
    'is_downloaded',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_downloaded" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _isActiveMeta = const VerificationMeta(
    'isActive',
  );
  @override
  late final GeneratedColumn<bool> isActive = GeneratedColumn<bool>(
    'is_active',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: false,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_active" IN (0, 1))',
    ),
    defaultValue: const Constant(false),
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    modelName,
    sizeBytes,
    localPath,
    isDownloaded,
    isActive,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'offline_model_weights';
  @override
  VerificationContext validateIntegrity(
    Insertable<OfflineModelWeight> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('model_name')) {
      context.handle(
        _modelNameMeta,
        modelName.isAcceptableOrUnknown(data['model_name']!, _modelNameMeta),
      );
    } else if (isInserting) {
      context.missing(_modelNameMeta);
    }
    if (data.containsKey('size_bytes')) {
      context.handle(
        _sizeBytesMeta,
        sizeBytes.isAcceptableOrUnknown(data['size_bytes']!, _sizeBytesMeta),
      );
    } else if (isInserting) {
      context.missing(_sizeBytesMeta);
    }
    if (data.containsKey('local_path')) {
      context.handle(
        _localPathMeta,
        localPath.isAcceptableOrUnknown(data['local_path']!, _localPathMeta),
      );
    }
    if (data.containsKey('is_downloaded')) {
      context.handle(
        _isDownloadedMeta,
        isDownloaded.isAcceptableOrUnknown(
          data['is_downloaded']!,
          _isDownloadedMeta,
        ),
      );
    }
    if (data.containsKey('is_active')) {
      context.handle(
        _isActiveMeta,
        isActive.isAcceptableOrUnknown(data['is_active']!, _isActiveMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  OfflineModelWeight map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return OfflineModelWeight(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      modelName: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}model_name'],
      )!,
      sizeBytes: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}size_bytes'],
      )!,
      localPath: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}local_path'],
      ),
      isDownloaded: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_downloaded'],
      )!,
      isActive: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_active'],
      )!,
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      )!,
    );
  }

  @override
  $OfflineModelWeightsTable createAlias(String alias) {
    return $OfflineModelWeightsTable(attachedDatabase, alias);
  }
}

class OfflineModelWeight extends DataClass
    implements Insertable<OfflineModelWeight> {
  final String id;
  final String modelName;
  final String sizeBytes;
  final String? localPath;
  final bool isDownloaded;
  final bool isActive;
  final DateTime updatedAt;
  const OfflineModelWeight({
    required this.id,
    required this.modelName,
    required this.sizeBytes,
    this.localPath,
    required this.isDownloaded,
    required this.isActive,
    required this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['model_name'] = Variable<String>(modelName);
    map['size_bytes'] = Variable<String>(sizeBytes);
    if (!nullToAbsent || localPath != null) {
      map['local_path'] = Variable<String>(localPath);
    }
    map['is_downloaded'] = Variable<bool>(isDownloaded);
    map['is_active'] = Variable<bool>(isActive);
    map['updated_at'] = Variable<DateTime>(updatedAt);
    return map;
  }

  OfflineModelWeightsCompanion toCompanion(bool nullToAbsent) {
    return OfflineModelWeightsCompanion(
      id: Value(id),
      modelName: Value(modelName),
      sizeBytes: Value(sizeBytes),
      localPath: localPath == null && nullToAbsent
          ? const Value.absent()
          : Value(localPath),
      isDownloaded: Value(isDownloaded),
      isActive: Value(isActive),
      updatedAt: Value(updatedAt),
    );
  }

  factory OfflineModelWeight.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return OfflineModelWeight(
      id: serializer.fromJson<String>(json['id']),
      modelName: serializer.fromJson<String>(json['modelName']),
      sizeBytes: serializer.fromJson<String>(json['sizeBytes']),
      localPath: serializer.fromJson<String?>(json['localPath']),
      isDownloaded: serializer.fromJson<bool>(json['isDownloaded']),
      isActive: serializer.fromJson<bool>(json['isActive']),
      updatedAt: serializer.fromJson<DateTime>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'modelName': serializer.toJson<String>(modelName),
      'sizeBytes': serializer.toJson<String>(sizeBytes),
      'localPath': serializer.toJson<String?>(localPath),
      'isDownloaded': serializer.toJson<bool>(isDownloaded),
      'isActive': serializer.toJson<bool>(isActive),
      'updatedAt': serializer.toJson<DateTime>(updatedAt),
    };
  }

  OfflineModelWeight copyWith({
    String? id,
    String? modelName,
    String? sizeBytes,
    Value<String?> localPath = const Value.absent(),
    bool? isDownloaded,
    bool? isActive,
    DateTime? updatedAt,
  }) => OfflineModelWeight(
    id: id ?? this.id,
    modelName: modelName ?? this.modelName,
    sizeBytes: sizeBytes ?? this.sizeBytes,
    localPath: localPath.present ? localPath.value : this.localPath,
    isDownloaded: isDownloaded ?? this.isDownloaded,
    isActive: isActive ?? this.isActive,
    updatedAt: updatedAt ?? this.updatedAt,
  );
  OfflineModelWeight copyWithCompanion(OfflineModelWeightsCompanion data) {
    return OfflineModelWeight(
      id: data.id.present ? data.id.value : this.id,
      modelName: data.modelName.present ? data.modelName.value : this.modelName,
      sizeBytes: data.sizeBytes.present ? data.sizeBytes.value : this.sizeBytes,
      localPath: data.localPath.present ? data.localPath.value : this.localPath,
      isDownloaded: data.isDownloaded.present
          ? data.isDownloaded.value
          : this.isDownloaded,
      isActive: data.isActive.present ? data.isActive.value : this.isActive,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('OfflineModelWeight(')
          ..write('id: $id, ')
          ..write('modelName: $modelName, ')
          ..write('sizeBytes: $sizeBytes, ')
          ..write('localPath: $localPath, ')
          ..write('isDownloaded: $isDownloaded, ')
          ..write('isActive: $isActive, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    modelName,
    sizeBytes,
    localPath,
    isDownloaded,
    isActive,
    updatedAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is OfflineModelWeight &&
          other.id == this.id &&
          other.modelName == this.modelName &&
          other.sizeBytes == this.sizeBytes &&
          other.localPath == this.localPath &&
          other.isDownloaded == this.isDownloaded &&
          other.isActive == this.isActive &&
          other.updatedAt == this.updatedAt);
}

class OfflineModelWeightsCompanion extends UpdateCompanion<OfflineModelWeight> {
  final Value<String> id;
  final Value<String> modelName;
  final Value<String> sizeBytes;
  final Value<String?> localPath;
  final Value<bool> isDownloaded;
  final Value<bool> isActive;
  final Value<DateTime> updatedAt;
  final Value<int> rowid;
  const OfflineModelWeightsCompanion({
    this.id = const Value.absent(),
    this.modelName = const Value.absent(),
    this.sizeBytes = const Value.absent(),
    this.localPath = const Value.absent(),
    this.isDownloaded = const Value.absent(),
    this.isActive = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  OfflineModelWeightsCompanion.insert({
    required String id,
    required String modelName,
    required String sizeBytes,
    this.localPath = const Value.absent(),
    this.isDownloaded = const Value.absent(),
    this.isActive = const Value.absent(),
    required DateTime updatedAt,
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       modelName = Value(modelName),
       sizeBytes = Value(sizeBytes),
       updatedAt = Value(updatedAt);
  static Insertable<OfflineModelWeight> custom({
    Expression<String>? id,
    Expression<String>? modelName,
    Expression<String>? sizeBytes,
    Expression<String>? localPath,
    Expression<bool>? isDownloaded,
    Expression<bool>? isActive,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (modelName != null) 'model_name': modelName,
      if (sizeBytes != null) 'size_bytes': sizeBytes,
      if (localPath != null) 'local_path': localPath,
      if (isDownloaded != null) 'is_downloaded': isDownloaded,
      if (isActive != null) 'is_active': isActive,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  OfflineModelWeightsCompanion copyWith({
    Value<String>? id,
    Value<String>? modelName,
    Value<String>? sizeBytes,
    Value<String?>? localPath,
    Value<bool>? isDownloaded,
    Value<bool>? isActive,
    Value<DateTime>? updatedAt,
    Value<int>? rowid,
  }) {
    return OfflineModelWeightsCompanion(
      id: id ?? this.id,
      modelName: modelName ?? this.modelName,
      sizeBytes: sizeBytes ?? this.sizeBytes,
      localPath: localPath ?? this.localPath,
      isDownloaded: isDownloaded ?? this.isDownloaded,
      isActive: isActive ?? this.isActive,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (modelName.present) {
      map['model_name'] = Variable<String>(modelName.value);
    }
    if (sizeBytes.present) {
      map['size_bytes'] = Variable<String>(sizeBytes.value);
    }
    if (localPath.present) {
      map['local_path'] = Variable<String>(localPath.value);
    }
    if (isDownloaded.present) {
      map['is_downloaded'] = Variable<bool>(isDownloaded.value);
    }
    if (isActive.present) {
      map['is_active'] = Variable<bool>(isActive.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('OfflineModelWeightsCompanion(')
          ..write('id: $id, ')
          ..write('modelName: $modelName, ')
          ..write('sizeBytes: $sizeBytes, ')
          ..write('localPath: $localPath, ')
          ..write('isDownloaded: $isDownloaded, ')
          ..write('isActive: $isActive, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $TelemetryLogsTable extends TelemetryLogs
    with TableInfo<$TelemetryLogsTable, TelemetryLog> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $TelemetryLogsTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _eventTypeMeta = const VerificationMeta(
    'eventType',
  );
  @override
  late final GeneratedColumn<String> eventType = GeneratedColumn<String>(
    'event_type',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _latencyMsMeta = const VerificationMeta(
    'latencyMs',
  );
  @override
  late final GeneratedColumn<double> latencyMs = GeneratedColumn<double>(
    'latency_ms',
    aliasedName,
    false,
    type: DriftSqlType.double,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _agentUsedMeta = const VerificationMeta(
    'agentUsed',
  );
  @override
  late final GeneratedColumn<String> agentUsed = GeneratedColumn<String>(
    'agent_used',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _modelUsedMeta = const VerificationMeta(
    'modelUsed',
  );
  @override
  late final GeneratedColumn<String> modelUsed = GeneratedColumn<String>(
    'model_used',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _isOfflineMeta = const VerificationMeta(
    'isOffline',
  );
  @override
  late final GeneratedColumn<bool> isOffline = GeneratedColumn<bool>(
    'is_offline',
    aliasedName,
    false,
    type: DriftSqlType.bool,
    requiredDuringInsert: true,
    defaultConstraints: GeneratedColumn.constraintIsAlways(
      'CHECK ("is_offline" IN (0, 1))',
    ),
  );
  static const VerificationMeta _errorMessageMeta = const VerificationMeta(
    'errorMessage',
  );
  @override
  late final GeneratedColumn<String> errorMessage = GeneratedColumn<String>(
    'error_message',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _feedbackRatingMeta = const VerificationMeta(
    'feedbackRating',
  );
  @override
  late final GeneratedColumn<int> feedbackRating = GeneratedColumn<int>(
    'feedback_rating',
    aliasedName,
    true,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    eventType,
    latencyMs,
    agentUsed,
    modelUsed,
    isOffline,
    errorMessage,
    feedbackRating,
    createdAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'telemetry_logs';
  @override
  VerificationContext validateIntegrity(
    Insertable<TelemetryLog> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('event_type')) {
      context.handle(
        _eventTypeMeta,
        eventType.isAcceptableOrUnknown(data['event_type']!, _eventTypeMeta),
      );
    } else if (isInserting) {
      context.missing(_eventTypeMeta);
    }
    if (data.containsKey('latency_ms')) {
      context.handle(
        _latencyMsMeta,
        latencyMs.isAcceptableOrUnknown(data['latency_ms']!, _latencyMsMeta),
      );
    } else if (isInserting) {
      context.missing(_latencyMsMeta);
    }
    if (data.containsKey('agent_used')) {
      context.handle(
        _agentUsedMeta,
        agentUsed.isAcceptableOrUnknown(data['agent_used']!, _agentUsedMeta),
      );
    } else if (isInserting) {
      context.missing(_agentUsedMeta);
    }
    if (data.containsKey('model_used')) {
      context.handle(
        _modelUsedMeta,
        modelUsed.isAcceptableOrUnknown(data['model_used']!, _modelUsedMeta),
      );
    } else if (isInserting) {
      context.missing(_modelUsedMeta);
    }
    if (data.containsKey('is_offline')) {
      context.handle(
        _isOfflineMeta,
        isOffline.isAcceptableOrUnknown(data['is_offline']!, _isOfflineMeta),
      );
    } else if (isInserting) {
      context.missing(_isOfflineMeta);
    }
    if (data.containsKey('error_message')) {
      context.handle(
        _errorMessageMeta,
        errorMessage.isAcceptableOrUnknown(
          data['error_message']!,
          _errorMessageMeta,
        ),
      );
    }
    if (data.containsKey('feedback_rating')) {
      context.handle(
        _feedbackRatingMeta,
        feedbackRating.isAcceptableOrUnknown(
          data['feedback_rating']!,
          _feedbackRatingMeta,
        ),
      );
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  TelemetryLog map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return TelemetryLog(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      eventType: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}event_type'],
      )!,
      latencyMs: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}latency_ms'],
      )!,
      agentUsed: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}agent_used'],
      )!,
      modelUsed: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}model_used'],
      )!,
      isOffline: attachedDatabase.typeMapping.read(
        DriftSqlType.bool,
        data['${effectivePrefix}is_offline'],
      )!,
      errorMessage: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}error_message'],
      ),
      feedbackRating: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}feedback_rating'],
      ),
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
    );
  }

  @override
  $TelemetryLogsTable createAlias(String alias) {
    return $TelemetryLogsTable(attachedDatabase, alias);
  }
}

class TelemetryLog extends DataClass implements Insertable<TelemetryLog> {
  final String id;
  final String eventType;
  final double latencyMs;
  final String agentUsed;
  final String modelUsed;
  final bool isOffline;
  final String? errorMessage;
  final int? feedbackRating;
  final DateTime createdAt;
  const TelemetryLog({
    required this.id,
    required this.eventType,
    required this.latencyMs,
    required this.agentUsed,
    required this.modelUsed,
    required this.isOffline,
    this.errorMessage,
    this.feedbackRating,
    required this.createdAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['event_type'] = Variable<String>(eventType);
    map['latency_ms'] = Variable<double>(latencyMs);
    map['agent_used'] = Variable<String>(agentUsed);
    map['model_used'] = Variable<String>(modelUsed);
    map['is_offline'] = Variable<bool>(isOffline);
    if (!nullToAbsent || errorMessage != null) {
      map['error_message'] = Variable<String>(errorMessage);
    }
    if (!nullToAbsent || feedbackRating != null) {
      map['feedback_rating'] = Variable<int>(feedbackRating);
    }
    map['created_at'] = Variable<DateTime>(createdAt);
    return map;
  }

  TelemetryLogsCompanion toCompanion(bool nullToAbsent) {
    return TelemetryLogsCompanion(
      id: Value(id),
      eventType: Value(eventType),
      latencyMs: Value(latencyMs),
      agentUsed: Value(agentUsed),
      modelUsed: Value(modelUsed),
      isOffline: Value(isOffline),
      errorMessage: errorMessage == null && nullToAbsent
          ? const Value.absent()
          : Value(errorMessage),
      feedbackRating: feedbackRating == null && nullToAbsent
          ? const Value.absent()
          : Value(feedbackRating),
      createdAt: Value(createdAt),
    );
  }

  factory TelemetryLog.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return TelemetryLog(
      id: serializer.fromJson<String>(json['id']),
      eventType: serializer.fromJson<String>(json['eventType']),
      latencyMs: serializer.fromJson<double>(json['latencyMs']),
      agentUsed: serializer.fromJson<String>(json['agentUsed']),
      modelUsed: serializer.fromJson<String>(json['modelUsed']),
      isOffline: serializer.fromJson<bool>(json['isOffline']),
      errorMessage: serializer.fromJson<String?>(json['errorMessage']),
      feedbackRating: serializer.fromJson<int?>(json['feedbackRating']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'eventType': serializer.toJson<String>(eventType),
      'latencyMs': serializer.toJson<double>(latencyMs),
      'agentUsed': serializer.toJson<String>(agentUsed),
      'modelUsed': serializer.toJson<String>(modelUsed),
      'isOffline': serializer.toJson<bool>(isOffline),
      'errorMessage': serializer.toJson<String?>(errorMessage),
      'feedbackRating': serializer.toJson<int?>(feedbackRating),
      'createdAt': serializer.toJson<DateTime>(createdAt),
    };
  }

  TelemetryLog copyWith({
    String? id,
    String? eventType,
    double? latencyMs,
    String? agentUsed,
    String? modelUsed,
    bool? isOffline,
    Value<String?> errorMessage = const Value.absent(),
    Value<int?> feedbackRating = const Value.absent(),
    DateTime? createdAt,
  }) => TelemetryLog(
    id: id ?? this.id,
    eventType: eventType ?? this.eventType,
    latencyMs: latencyMs ?? this.latencyMs,
    agentUsed: agentUsed ?? this.agentUsed,
    modelUsed: modelUsed ?? this.modelUsed,
    isOffline: isOffline ?? this.isOffline,
    errorMessage: errorMessage.present ? errorMessage.value : this.errorMessage,
    feedbackRating: feedbackRating.present
        ? feedbackRating.value
        : this.feedbackRating,
    createdAt: createdAt ?? this.createdAt,
  );
  TelemetryLog copyWithCompanion(TelemetryLogsCompanion data) {
    return TelemetryLog(
      id: data.id.present ? data.id.value : this.id,
      eventType: data.eventType.present ? data.eventType.value : this.eventType,
      latencyMs: data.latencyMs.present ? data.latencyMs.value : this.latencyMs,
      agentUsed: data.agentUsed.present ? data.agentUsed.value : this.agentUsed,
      modelUsed: data.modelUsed.present ? data.modelUsed.value : this.modelUsed,
      isOffline: data.isOffline.present ? data.isOffline.value : this.isOffline,
      errorMessage: data.errorMessage.present
          ? data.errorMessage.value
          : this.errorMessage,
      feedbackRating: data.feedbackRating.present
          ? data.feedbackRating.value
          : this.feedbackRating,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('TelemetryLog(')
          ..write('id: $id, ')
          ..write('eventType: $eventType, ')
          ..write('latencyMs: $latencyMs, ')
          ..write('agentUsed: $agentUsed, ')
          ..write('modelUsed: $modelUsed, ')
          ..write('isOffline: $isOffline, ')
          ..write('errorMessage: $errorMessage, ')
          ..write('feedbackRating: $feedbackRating, ')
          ..write('createdAt: $createdAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    eventType,
    latencyMs,
    agentUsed,
    modelUsed,
    isOffline,
    errorMessage,
    feedbackRating,
    createdAt,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is TelemetryLog &&
          other.id == this.id &&
          other.eventType == this.eventType &&
          other.latencyMs == this.latencyMs &&
          other.agentUsed == this.agentUsed &&
          other.modelUsed == this.modelUsed &&
          other.isOffline == this.isOffline &&
          other.errorMessage == this.errorMessage &&
          other.feedbackRating == this.feedbackRating &&
          other.createdAt == this.createdAt);
}

class TelemetryLogsCompanion extends UpdateCompanion<TelemetryLog> {
  final Value<String> id;
  final Value<String> eventType;
  final Value<double> latencyMs;
  final Value<String> agentUsed;
  final Value<String> modelUsed;
  final Value<bool> isOffline;
  final Value<String?> errorMessage;
  final Value<int?> feedbackRating;
  final Value<DateTime> createdAt;
  final Value<int> rowid;
  const TelemetryLogsCompanion({
    this.id = const Value.absent(),
    this.eventType = const Value.absent(),
    this.latencyMs = const Value.absent(),
    this.agentUsed = const Value.absent(),
    this.modelUsed = const Value.absent(),
    this.isOffline = const Value.absent(),
    this.errorMessage = const Value.absent(),
    this.feedbackRating = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  TelemetryLogsCompanion.insert({
    required String id,
    required String eventType,
    required double latencyMs,
    required String agentUsed,
    required String modelUsed,
    required bool isOffline,
    this.errorMessage = const Value.absent(),
    this.feedbackRating = const Value.absent(),
    required DateTime createdAt,
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       eventType = Value(eventType),
       latencyMs = Value(latencyMs),
       agentUsed = Value(agentUsed),
       modelUsed = Value(modelUsed),
       isOffline = Value(isOffline),
       createdAt = Value(createdAt);
  static Insertable<TelemetryLog> custom({
    Expression<String>? id,
    Expression<String>? eventType,
    Expression<double>? latencyMs,
    Expression<String>? agentUsed,
    Expression<String>? modelUsed,
    Expression<bool>? isOffline,
    Expression<String>? errorMessage,
    Expression<int>? feedbackRating,
    Expression<DateTime>? createdAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (eventType != null) 'event_type': eventType,
      if (latencyMs != null) 'latency_ms': latencyMs,
      if (agentUsed != null) 'agent_used': agentUsed,
      if (modelUsed != null) 'model_used': modelUsed,
      if (isOffline != null) 'is_offline': isOffline,
      if (errorMessage != null) 'error_message': errorMessage,
      if (feedbackRating != null) 'feedback_rating': feedbackRating,
      if (createdAt != null) 'created_at': createdAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  TelemetryLogsCompanion copyWith({
    Value<String>? id,
    Value<String>? eventType,
    Value<double>? latencyMs,
    Value<String>? agentUsed,
    Value<String>? modelUsed,
    Value<bool>? isOffline,
    Value<String?>? errorMessage,
    Value<int?>? feedbackRating,
    Value<DateTime>? createdAt,
    Value<int>? rowid,
  }) {
    return TelemetryLogsCompanion(
      id: id ?? this.id,
      eventType: eventType ?? this.eventType,
      latencyMs: latencyMs ?? this.latencyMs,
      agentUsed: agentUsed ?? this.agentUsed,
      modelUsed: modelUsed ?? this.modelUsed,
      isOffline: isOffline ?? this.isOffline,
      errorMessage: errorMessage ?? this.errorMessage,
      feedbackRating: feedbackRating ?? this.feedbackRating,
      createdAt: createdAt ?? this.createdAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (eventType.present) {
      map['event_type'] = Variable<String>(eventType.value);
    }
    if (latencyMs.present) {
      map['latency_ms'] = Variable<double>(latencyMs.value);
    }
    if (agentUsed.present) {
      map['agent_used'] = Variable<String>(agentUsed.value);
    }
    if (modelUsed.present) {
      map['model_used'] = Variable<String>(modelUsed.value);
    }
    if (isOffline.present) {
      map['is_offline'] = Variable<bool>(isOffline.value);
    }
    if (errorMessage.present) {
      map['error_message'] = Variable<String>(errorMessage.value);
    }
    if (feedbackRating.present) {
      map['feedback_rating'] = Variable<int>(feedbackRating.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('TelemetryLogsCompanion(')
          ..write('id: $id, ')
          ..write('eventType: $eventType, ')
          ..write('latencyMs: $latencyMs, ')
          ..write('agentUsed: $agentUsed, ')
          ..write('modelUsed: $modelUsed, ')
          ..write('isOffline: $isOffline, ')
          ..write('errorMessage: $errorMessage, ')
          ..write('feedbackRating: $feedbackRating, ')
          ..write('createdAt: $createdAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $OutboxEntriesTable extends OutboxEntries
    with TableInfo<$OutboxEntriesTable, OutboxEntry> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $OutboxEntriesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _targetTableMeta = const VerificationMeta(
    'targetTable',
  );
  @override
  late final GeneratedColumn<String> targetTable = GeneratedColumn<String>(
    'target_table',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _recordIdMeta = const VerificationMeta(
    'recordId',
  );
  @override
  late final GeneratedColumn<String> recordId = GeneratedColumn<String>(
    'record_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _operationMeta = const VerificationMeta(
    'operation',
  );
  @override
  late final GeneratedColumn<String> operation = GeneratedColumn<String>(
    'operation',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _payloadJsonMeta = const VerificationMeta(
    'payloadJson',
  );
  @override
  late final GeneratedColumn<String> payloadJson = GeneratedColumn<String>(
    'payload_json',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _clientUpdatedAtMeta = const VerificationMeta(
    'clientUpdatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> clientUpdatedAt =
      GeneratedColumn<DateTime>(
        'client_updated_at',
        aliasedName,
        false,
        type: DriftSqlType.dateTime,
        requiredDuringInsert: true,
      );
  static const VerificationMeta _createdAtMeta = const VerificationMeta(
    'createdAt',
  );
  @override
  late final GeneratedColumn<DateTime> createdAt = GeneratedColumn<DateTime>(
    'created_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _attemptsMeta = const VerificationMeta(
    'attempts',
  );
  @override
  late final GeneratedColumn<int> attempts = GeneratedColumn<int>(
    'attempts',
    aliasedName,
    false,
    type: DriftSqlType.int,
    requiredDuringInsert: false,
    defaultValue: const Constant(0),
  );
  static const VerificationMeta _lastErrorMeta = const VerificationMeta(
    'lastError',
  );
  @override
  late final GeneratedColumn<String> lastError = GeneratedColumn<String>(
    'last_error',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    targetTable,
    recordId,
    operation,
    payloadJson,
    clientUpdatedAt,
    createdAt,
    attempts,
    lastError,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'outbox_entries';
  @override
  VerificationContext validateIntegrity(
    Insertable<OutboxEntry> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('target_table')) {
      context.handle(
        _targetTableMeta,
        targetTable.isAcceptableOrUnknown(
          data['target_table']!,
          _targetTableMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_targetTableMeta);
    }
    if (data.containsKey('record_id')) {
      context.handle(
        _recordIdMeta,
        recordId.isAcceptableOrUnknown(data['record_id']!, _recordIdMeta),
      );
    } else if (isInserting) {
      context.missing(_recordIdMeta);
    }
    if (data.containsKey('operation')) {
      context.handle(
        _operationMeta,
        operation.isAcceptableOrUnknown(data['operation']!, _operationMeta),
      );
    } else if (isInserting) {
      context.missing(_operationMeta);
    }
    if (data.containsKey('payload_json')) {
      context.handle(
        _payloadJsonMeta,
        payloadJson.isAcceptableOrUnknown(
          data['payload_json']!,
          _payloadJsonMeta,
        ),
      );
    }
    if (data.containsKey('client_updated_at')) {
      context.handle(
        _clientUpdatedAtMeta,
        clientUpdatedAt.isAcceptableOrUnknown(
          data['client_updated_at']!,
          _clientUpdatedAtMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_clientUpdatedAtMeta);
    }
    if (data.containsKey('created_at')) {
      context.handle(
        _createdAtMeta,
        createdAt.isAcceptableOrUnknown(data['created_at']!, _createdAtMeta),
      );
    } else if (isInserting) {
      context.missing(_createdAtMeta);
    }
    if (data.containsKey('attempts')) {
      context.handle(
        _attemptsMeta,
        attempts.isAcceptableOrUnknown(data['attempts']!, _attemptsMeta),
      );
    }
    if (data.containsKey('last_error')) {
      context.handle(
        _lastErrorMeta,
        lastError.isAcceptableOrUnknown(data['last_error']!, _lastErrorMeta),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  OutboxEntry map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return OutboxEntry(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      targetTable: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}target_table'],
      )!,
      recordId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}record_id'],
      )!,
      operation: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}operation'],
      )!,
      payloadJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}payload_json'],
      ),
      clientUpdatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}client_updated_at'],
      )!,
      createdAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}created_at'],
      )!,
      attempts: attachedDatabase.typeMapping.read(
        DriftSqlType.int,
        data['${effectivePrefix}attempts'],
      )!,
      lastError: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}last_error'],
      ),
    );
  }

  @override
  $OutboxEntriesTable createAlias(String alias) {
    return $OutboxEntriesTable(attachedDatabase, alias);
  }
}

class OutboxEntry extends DataClass implements Insertable<OutboxEntry> {
  final String id;
  final String targetTable;
  final String recordId;
  final String operation;
  final String? payloadJson;
  final DateTime clientUpdatedAt;
  final DateTime createdAt;

  /// Failed pushes of this one mutation. At [kMaxOutboxAttempts] the row is
  /// *parked*: kept (so nothing is lost and the count is visible) but no
  /// longer sent, so one bad row can no longer hold up every other change
  /// (audit F-11). A server `error` marked non-retryable parks it at once.
  final int attempts;

  /// The server's `detail` for the last failure, for diagnosis.
  final String? lastError;
  const OutboxEntry({
    required this.id,
    required this.targetTable,
    required this.recordId,
    required this.operation,
    this.payloadJson,
    required this.clientUpdatedAt,
    required this.createdAt,
    required this.attempts,
    this.lastError,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['target_table'] = Variable<String>(targetTable);
    map['record_id'] = Variable<String>(recordId);
    map['operation'] = Variable<String>(operation);
    if (!nullToAbsent || payloadJson != null) {
      map['payload_json'] = Variable<String>(payloadJson);
    }
    map['client_updated_at'] = Variable<DateTime>(clientUpdatedAt);
    map['created_at'] = Variable<DateTime>(createdAt);
    map['attempts'] = Variable<int>(attempts);
    if (!nullToAbsent || lastError != null) {
      map['last_error'] = Variable<String>(lastError);
    }
    return map;
  }

  OutboxEntriesCompanion toCompanion(bool nullToAbsent) {
    return OutboxEntriesCompanion(
      id: Value(id),
      targetTable: Value(targetTable),
      recordId: Value(recordId),
      operation: Value(operation),
      payloadJson: payloadJson == null && nullToAbsent
          ? const Value.absent()
          : Value(payloadJson),
      clientUpdatedAt: Value(clientUpdatedAt),
      createdAt: Value(createdAt),
      attempts: Value(attempts),
      lastError: lastError == null && nullToAbsent
          ? const Value.absent()
          : Value(lastError),
    );
  }

  factory OutboxEntry.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return OutboxEntry(
      id: serializer.fromJson<String>(json['id']),
      targetTable: serializer.fromJson<String>(json['targetTable']),
      recordId: serializer.fromJson<String>(json['recordId']),
      operation: serializer.fromJson<String>(json['operation']),
      payloadJson: serializer.fromJson<String?>(json['payloadJson']),
      clientUpdatedAt: serializer.fromJson<DateTime>(json['clientUpdatedAt']),
      createdAt: serializer.fromJson<DateTime>(json['createdAt']),
      attempts: serializer.fromJson<int>(json['attempts']),
      lastError: serializer.fromJson<String?>(json['lastError']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'targetTable': serializer.toJson<String>(targetTable),
      'recordId': serializer.toJson<String>(recordId),
      'operation': serializer.toJson<String>(operation),
      'payloadJson': serializer.toJson<String?>(payloadJson),
      'clientUpdatedAt': serializer.toJson<DateTime>(clientUpdatedAt),
      'createdAt': serializer.toJson<DateTime>(createdAt),
      'attempts': serializer.toJson<int>(attempts),
      'lastError': serializer.toJson<String?>(lastError),
    };
  }

  OutboxEntry copyWith({
    String? id,
    String? targetTable,
    String? recordId,
    String? operation,
    Value<String?> payloadJson = const Value.absent(),
    DateTime? clientUpdatedAt,
    DateTime? createdAt,
    int? attempts,
    Value<String?> lastError = const Value.absent(),
  }) => OutboxEntry(
    id: id ?? this.id,
    targetTable: targetTable ?? this.targetTable,
    recordId: recordId ?? this.recordId,
    operation: operation ?? this.operation,
    payloadJson: payloadJson.present ? payloadJson.value : this.payloadJson,
    clientUpdatedAt: clientUpdatedAt ?? this.clientUpdatedAt,
    createdAt: createdAt ?? this.createdAt,
    attempts: attempts ?? this.attempts,
    lastError: lastError.present ? lastError.value : this.lastError,
  );
  OutboxEntry copyWithCompanion(OutboxEntriesCompanion data) {
    return OutboxEntry(
      id: data.id.present ? data.id.value : this.id,
      targetTable: data.targetTable.present
          ? data.targetTable.value
          : this.targetTable,
      recordId: data.recordId.present ? data.recordId.value : this.recordId,
      operation: data.operation.present ? data.operation.value : this.operation,
      payloadJson: data.payloadJson.present
          ? data.payloadJson.value
          : this.payloadJson,
      clientUpdatedAt: data.clientUpdatedAt.present
          ? data.clientUpdatedAt.value
          : this.clientUpdatedAt,
      createdAt: data.createdAt.present ? data.createdAt.value : this.createdAt,
      attempts: data.attempts.present ? data.attempts.value : this.attempts,
      lastError: data.lastError.present ? data.lastError.value : this.lastError,
    );
  }

  @override
  String toString() {
    return (StringBuffer('OutboxEntry(')
          ..write('id: $id, ')
          ..write('targetTable: $targetTable, ')
          ..write('recordId: $recordId, ')
          ..write('operation: $operation, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('clientUpdatedAt: $clientUpdatedAt, ')
          ..write('createdAt: $createdAt, ')
          ..write('attempts: $attempts, ')
          ..write('lastError: $lastError')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    id,
    targetTable,
    recordId,
    operation,
    payloadJson,
    clientUpdatedAt,
    createdAt,
    attempts,
    lastError,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is OutboxEntry &&
          other.id == this.id &&
          other.targetTable == this.targetTable &&
          other.recordId == this.recordId &&
          other.operation == this.operation &&
          other.payloadJson == this.payloadJson &&
          other.clientUpdatedAt == this.clientUpdatedAt &&
          other.createdAt == this.createdAt &&
          other.attempts == this.attempts &&
          other.lastError == this.lastError);
}

class OutboxEntriesCompanion extends UpdateCompanion<OutboxEntry> {
  final Value<String> id;
  final Value<String> targetTable;
  final Value<String> recordId;
  final Value<String> operation;
  final Value<String?> payloadJson;
  final Value<DateTime> clientUpdatedAt;
  final Value<DateTime> createdAt;
  final Value<int> attempts;
  final Value<String?> lastError;
  final Value<int> rowid;
  const OutboxEntriesCompanion({
    this.id = const Value.absent(),
    this.targetTable = const Value.absent(),
    this.recordId = const Value.absent(),
    this.operation = const Value.absent(),
    this.payloadJson = const Value.absent(),
    this.clientUpdatedAt = const Value.absent(),
    this.createdAt = const Value.absent(),
    this.attempts = const Value.absent(),
    this.lastError = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  OutboxEntriesCompanion.insert({
    required String id,
    required String targetTable,
    required String recordId,
    required String operation,
    this.payloadJson = const Value.absent(),
    required DateTime clientUpdatedAt,
    required DateTime createdAt,
    this.attempts = const Value.absent(),
    this.lastError = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       targetTable = Value(targetTable),
       recordId = Value(recordId),
       operation = Value(operation),
       clientUpdatedAt = Value(clientUpdatedAt),
       createdAt = Value(createdAt);
  static Insertable<OutboxEntry> custom({
    Expression<String>? id,
    Expression<String>? targetTable,
    Expression<String>? recordId,
    Expression<String>? operation,
    Expression<String>? payloadJson,
    Expression<DateTime>? clientUpdatedAt,
    Expression<DateTime>? createdAt,
    Expression<int>? attempts,
    Expression<String>? lastError,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (targetTable != null) 'target_table': targetTable,
      if (recordId != null) 'record_id': recordId,
      if (operation != null) 'operation': operation,
      if (payloadJson != null) 'payload_json': payloadJson,
      if (clientUpdatedAt != null) 'client_updated_at': clientUpdatedAt,
      if (createdAt != null) 'created_at': createdAt,
      if (attempts != null) 'attempts': attempts,
      if (lastError != null) 'last_error': lastError,
      if (rowid != null) 'rowid': rowid,
    });
  }

  OutboxEntriesCompanion copyWith({
    Value<String>? id,
    Value<String>? targetTable,
    Value<String>? recordId,
    Value<String>? operation,
    Value<String?>? payloadJson,
    Value<DateTime>? clientUpdatedAt,
    Value<DateTime>? createdAt,
    Value<int>? attempts,
    Value<String?>? lastError,
    Value<int>? rowid,
  }) {
    return OutboxEntriesCompanion(
      id: id ?? this.id,
      targetTable: targetTable ?? this.targetTable,
      recordId: recordId ?? this.recordId,
      operation: operation ?? this.operation,
      payloadJson: payloadJson ?? this.payloadJson,
      clientUpdatedAt: clientUpdatedAt ?? this.clientUpdatedAt,
      createdAt: createdAt ?? this.createdAt,
      attempts: attempts ?? this.attempts,
      lastError: lastError ?? this.lastError,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (targetTable.present) {
      map['target_table'] = Variable<String>(targetTable.value);
    }
    if (recordId.present) {
      map['record_id'] = Variable<String>(recordId.value);
    }
    if (operation.present) {
      map['operation'] = Variable<String>(operation.value);
    }
    if (payloadJson.present) {
      map['payload_json'] = Variable<String>(payloadJson.value);
    }
    if (clientUpdatedAt.present) {
      map['client_updated_at'] = Variable<DateTime>(clientUpdatedAt.value);
    }
    if (createdAt.present) {
      map['created_at'] = Variable<DateTime>(createdAt.value);
    }
    if (attempts.present) {
      map['attempts'] = Variable<int>(attempts.value);
    }
    if (lastError.present) {
      map['last_error'] = Variable<String>(lastError.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('OutboxEntriesCompanion(')
          ..write('id: $id, ')
          ..write('targetTable: $targetTable, ')
          ..write('recordId: $recordId, ')
          ..write('operation: $operation, ')
          ..write('payloadJson: $payloadJson, ')
          ..write('clientUpdatedAt: $clientUpdatedAt, ')
          ..write('createdAt: $createdAt, ')
          ..write('attempts: $attempts, ')
          ..write('lastError: $lastError, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $SyncMetadataTable extends SyncMetadata
    with TableInfo<$SyncMetadataTable, SyncMetadataData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $SyncMetadataTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _keyMeta = const VerificationMeta('key');
  @override
  late final GeneratedColumn<String> key = GeneratedColumn<String>(
    'key',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _valueMeta = const VerificationMeta('value');
  @override
  late final GeneratedColumn<String> value = GeneratedColumn<String>(
    'value',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [key, value];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'sync_metadata';
  @override
  VerificationContext validateIntegrity(
    Insertable<SyncMetadataData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('key')) {
      context.handle(
        _keyMeta,
        key.isAcceptableOrUnknown(data['key']!, _keyMeta),
      );
    } else if (isInserting) {
      context.missing(_keyMeta);
    }
    if (data.containsKey('value')) {
      context.handle(
        _valueMeta,
        value.isAcceptableOrUnknown(data['value']!, _valueMeta),
      );
    } else if (isInserting) {
      context.missing(_valueMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {key};
  @override
  SyncMetadataData map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return SyncMetadataData(
      key: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}key'],
      )!,
      value: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}value'],
      )!,
    );
  }

  @override
  $SyncMetadataTable createAlias(String alias) {
    return $SyncMetadataTable(attachedDatabase, alias);
  }
}

class SyncMetadataData extends DataClass
    implements Insertable<SyncMetadataData> {
  final String key;
  final String value;
  const SyncMetadataData({required this.key, required this.value});
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['key'] = Variable<String>(key);
    map['value'] = Variable<String>(value);
    return map;
  }

  SyncMetadataCompanion toCompanion(bool nullToAbsent) {
    return SyncMetadataCompanion(key: Value(key), value: Value(value));
  }

  factory SyncMetadataData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return SyncMetadataData(
      key: serializer.fromJson<String>(json['key']),
      value: serializer.fromJson<String>(json['value']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'key': serializer.toJson<String>(key),
      'value': serializer.toJson<String>(value),
    };
  }

  SyncMetadataData copyWith({String? key, String? value}) =>
      SyncMetadataData(key: key ?? this.key, value: value ?? this.value);
  SyncMetadataData copyWithCompanion(SyncMetadataCompanion data) {
    return SyncMetadataData(
      key: data.key.present ? data.key.value : this.key,
      value: data.value.present ? data.value.value : this.value,
    );
  }

  @override
  String toString() {
    return (StringBuffer('SyncMetadataData(')
          ..write('key: $key, ')
          ..write('value: $value')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(key, value);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is SyncMetadataData &&
          other.key == this.key &&
          other.value == this.value);
}

class SyncMetadataCompanion extends UpdateCompanion<SyncMetadataData> {
  final Value<String> key;
  final Value<String> value;
  final Value<int> rowid;
  const SyncMetadataCompanion({
    this.key = const Value.absent(),
    this.value = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  SyncMetadataCompanion.insert({
    required String key,
    required String value,
    this.rowid = const Value.absent(),
  }) : key = Value(key),
       value = Value(value);
  static Insertable<SyncMetadataData> custom({
    Expression<String>? key,
    Expression<String>? value,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (key != null) 'key': key,
      if (value != null) 'value': value,
      if (rowid != null) 'rowid': rowid,
    });
  }

  SyncMetadataCompanion copyWith({
    Value<String>? key,
    Value<String>? value,
    Value<int>? rowid,
  }) {
    return SyncMetadataCompanion(
      key: key ?? this.key,
      value: value ?? this.value,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (key.present) {
      map['key'] = Variable<String>(key.value);
    }
    if (value.present) {
      map['value'] = Variable<String>(value.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('SyncMetadataCompanion(')
          ..write('key: $key, ')
          ..write('value: $value, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $DesktopWorkspacesTable extends DesktopWorkspaces
    with TableInfo<$DesktopWorkspacesTable, DesktopWorkspace> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $DesktopWorkspacesTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _idMeta = const VerificationMeta('id');
  @override
  late final GeneratedColumn<String> id = GeneratedColumn<String>(
    'id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _rootPathMeta = const VerificationMeta(
    'rootPath',
  );
  @override
  late final GeneratedColumn<String> rootPath = GeneratedColumn<String>(
    'root_path',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _displayNameMeta = const VerificationMeta(
    'displayName',
  );
  @override
  late final GeneratedColumn<String> displayName = GeneratedColumn<String>(
    'display_name',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _lastOpenedAtMeta = const VerificationMeta(
    'lastOpenedAt',
  );
  @override
  late final GeneratedColumn<DateTime> lastOpenedAt = GeneratedColumn<DateTime>(
    'last_opened_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    id,
    rootPath,
    displayName,
    lastOpenedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'desktop_workspaces';
  @override
  VerificationContext validateIntegrity(
    Insertable<DesktopWorkspace> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('id')) {
      context.handle(_idMeta, id.isAcceptableOrUnknown(data['id']!, _idMeta));
    } else if (isInserting) {
      context.missing(_idMeta);
    }
    if (data.containsKey('root_path')) {
      context.handle(
        _rootPathMeta,
        rootPath.isAcceptableOrUnknown(data['root_path']!, _rootPathMeta),
      );
    } else if (isInserting) {
      context.missing(_rootPathMeta);
    }
    if (data.containsKey('display_name')) {
      context.handle(
        _displayNameMeta,
        displayName.isAcceptableOrUnknown(
          data['display_name']!,
          _displayNameMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_displayNameMeta);
    }
    if (data.containsKey('last_opened_at')) {
      context.handle(
        _lastOpenedAtMeta,
        lastOpenedAt.isAcceptableOrUnknown(
          data['last_opened_at']!,
          _lastOpenedAtMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_lastOpenedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {id};
  @override
  DesktopWorkspace map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DesktopWorkspace(
      id: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}id'],
      )!,
      rootPath: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}root_path'],
      )!,
      displayName: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}display_name'],
      )!,
      lastOpenedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}last_opened_at'],
      )!,
    );
  }

  @override
  $DesktopWorkspacesTable createAlias(String alias) {
    return $DesktopWorkspacesTable(attachedDatabase, alias);
  }
}

class DesktopWorkspace extends DataClass
    implements Insertable<DesktopWorkspace> {
  final String id;
  final String rootPath;
  final String displayName;
  final DateTime lastOpenedAt;
  const DesktopWorkspace({
    required this.id,
    required this.rootPath,
    required this.displayName,
    required this.lastOpenedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['id'] = Variable<String>(id);
    map['root_path'] = Variable<String>(rootPath);
    map['display_name'] = Variable<String>(displayName);
    map['last_opened_at'] = Variable<DateTime>(lastOpenedAt);
    return map;
  }

  DesktopWorkspacesCompanion toCompanion(bool nullToAbsent) {
    return DesktopWorkspacesCompanion(
      id: Value(id),
      rootPath: Value(rootPath),
      displayName: Value(displayName),
      lastOpenedAt: Value(lastOpenedAt),
    );
  }

  factory DesktopWorkspace.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DesktopWorkspace(
      id: serializer.fromJson<String>(json['id']),
      rootPath: serializer.fromJson<String>(json['rootPath']),
      displayName: serializer.fromJson<String>(json['displayName']),
      lastOpenedAt: serializer.fromJson<DateTime>(json['lastOpenedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'id': serializer.toJson<String>(id),
      'rootPath': serializer.toJson<String>(rootPath),
      'displayName': serializer.toJson<String>(displayName),
      'lastOpenedAt': serializer.toJson<DateTime>(lastOpenedAt),
    };
  }

  DesktopWorkspace copyWith({
    String? id,
    String? rootPath,
    String? displayName,
    DateTime? lastOpenedAt,
  }) => DesktopWorkspace(
    id: id ?? this.id,
    rootPath: rootPath ?? this.rootPath,
    displayName: displayName ?? this.displayName,
    lastOpenedAt: lastOpenedAt ?? this.lastOpenedAt,
  );
  DesktopWorkspace copyWithCompanion(DesktopWorkspacesCompanion data) {
    return DesktopWorkspace(
      id: data.id.present ? data.id.value : this.id,
      rootPath: data.rootPath.present ? data.rootPath.value : this.rootPath,
      displayName: data.displayName.present
          ? data.displayName.value
          : this.displayName,
      lastOpenedAt: data.lastOpenedAt.present
          ? data.lastOpenedAt.value
          : this.lastOpenedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DesktopWorkspace(')
          ..write('id: $id, ')
          ..write('rootPath: $rootPath, ')
          ..write('displayName: $displayName, ')
          ..write('lastOpenedAt: $lastOpenedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(id, rootPath, displayName, lastOpenedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DesktopWorkspace &&
          other.id == this.id &&
          other.rootPath == this.rootPath &&
          other.displayName == this.displayName &&
          other.lastOpenedAt == this.lastOpenedAt);
}

class DesktopWorkspacesCompanion extends UpdateCompanion<DesktopWorkspace> {
  final Value<String> id;
  final Value<String> rootPath;
  final Value<String> displayName;
  final Value<DateTime> lastOpenedAt;
  final Value<int> rowid;
  const DesktopWorkspacesCompanion({
    this.id = const Value.absent(),
    this.rootPath = const Value.absent(),
    this.displayName = const Value.absent(),
    this.lastOpenedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  DesktopWorkspacesCompanion.insert({
    required String id,
    required String rootPath,
    required String displayName,
    required DateTime lastOpenedAt,
    this.rowid = const Value.absent(),
  }) : id = Value(id),
       rootPath = Value(rootPath),
       displayName = Value(displayName),
       lastOpenedAt = Value(lastOpenedAt);
  static Insertable<DesktopWorkspace> custom({
    Expression<String>? id,
    Expression<String>? rootPath,
    Expression<String>? displayName,
    Expression<DateTime>? lastOpenedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (id != null) 'id': id,
      if (rootPath != null) 'root_path': rootPath,
      if (displayName != null) 'display_name': displayName,
      if (lastOpenedAt != null) 'last_opened_at': lastOpenedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  DesktopWorkspacesCompanion copyWith({
    Value<String>? id,
    Value<String>? rootPath,
    Value<String>? displayName,
    Value<DateTime>? lastOpenedAt,
    Value<int>? rowid,
  }) {
    return DesktopWorkspacesCompanion(
      id: id ?? this.id,
      rootPath: rootPath ?? this.rootPath,
      displayName: displayName ?? this.displayName,
      lastOpenedAt: lastOpenedAt ?? this.lastOpenedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (id.present) {
      map['id'] = Variable<String>(id.value);
    }
    if (rootPath.present) {
      map['root_path'] = Variable<String>(rootPath.value);
    }
    if (displayName.present) {
      map['display_name'] = Variable<String>(displayName.value);
    }
    if (lastOpenedAt.present) {
      map['last_opened_at'] = Variable<DateTime>(lastOpenedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('DesktopWorkspacesCompanion(')
          ..write('id: $id, ')
          ..write('rootPath: $rootPath, ')
          ..write('displayName: $displayName, ')
          ..write('lastOpenedAt: $lastOpenedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $DesktopEditorStateTable extends DesktopEditorState
    with TableInfo<$DesktopEditorStateTable, DesktopEditorStateData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $DesktopEditorStateTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _workspaceIdMeta = const VerificationMeta(
    'workspaceId',
  );
  @override
  late final GeneratedColumn<String> workspaceId = GeneratedColumn<String>(
    'workspace_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _openTabsJsonMeta = const VerificationMeta(
    'openTabsJson',
  );
  @override
  late final GeneratedColumn<String> openTabsJson = GeneratedColumn<String>(
    'open_tabs_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _activeTabPathMeta = const VerificationMeta(
    'activeTabPath',
  );
  @override
  late final GeneratedColumn<String> activeTabPath = GeneratedColumn<String>(
    'active_tab_path',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _sidebarWidthMeta = const VerificationMeta(
    'sidebarWidth',
  );
  @override
  late final GeneratedColumn<double> sidebarWidth = GeneratedColumn<double>(
    'sidebar_width',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _agentPanelWidthMeta = const VerificationMeta(
    'agentPanelWidth',
  );
  @override
  late final GeneratedColumn<double> agentPanelWidth = GeneratedColumn<double>(
    'agent_panel_width',
    aliasedName,
    true,
    type: DriftSqlType.double,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _bottomPanelHeightMeta = const VerificationMeta(
    'bottomPanelHeight',
  );
  @override
  late final GeneratedColumn<double> bottomPanelHeight =
      GeneratedColumn<double>(
        'bottom_panel_height',
        aliasedName,
        true,
        type: DriftSqlType.double,
        requiredDuringInsert: false,
      );
  @override
  List<GeneratedColumn> get $columns => [
    workspaceId,
    openTabsJson,
    activeTabPath,
    sidebarWidth,
    agentPanelWidth,
    bottomPanelHeight,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'desktop_editor_state';
  @override
  VerificationContext validateIntegrity(
    Insertable<DesktopEditorStateData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('workspace_id')) {
      context.handle(
        _workspaceIdMeta,
        workspaceId.isAcceptableOrUnknown(
          data['workspace_id']!,
          _workspaceIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_workspaceIdMeta);
    }
    if (data.containsKey('open_tabs_json')) {
      context.handle(
        _openTabsJsonMeta,
        openTabsJson.isAcceptableOrUnknown(
          data['open_tabs_json']!,
          _openTabsJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_openTabsJsonMeta);
    }
    if (data.containsKey('active_tab_path')) {
      context.handle(
        _activeTabPathMeta,
        activeTabPath.isAcceptableOrUnknown(
          data['active_tab_path']!,
          _activeTabPathMeta,
        ),
      );
    }
    if (data.containsKey('sidebar_width')) {
      context.handle(
        _sidebarWidthMeta,
        sidebarWidth.isAcceptableOrUnknown(
          data['sidebar_width']!,
          _sidebarWidthMeta,
        ),
      );
    }
    if (data.containsKey('agent_panel_width')) {
      context.handle(
        _agentPanelWidthMeta,
        agentPanelWidth.isAcceptableOrUnknown(
          data['agent_panel_width']!,
          _agentPanelWidthMeta,
        ),
      );
    }
    if (data.containsKey('bottom_panel_height')) {
      context.handle(
        _bottomPanelHeightMeta,
        bottomPanelHeight.isAcceptableOrUnknown(
          data['bottom_panel_height']!,
          _bottomPanelHeightMeta,
        ),
      );
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {workspaceId};
  @override
  DesktopEditorStateData map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DesktopEditorStateData(
      workspaceId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}workspace_id'],
      )!,
      openTabsJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}open_tabs_json'],
      )!,
      activeTabPath: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}active_tab_path'],
      ),
      sidebarWidth: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}sidebar_width'],
      ),
      agentPanelWidth: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}agent_panel_width'],
      ),
      bottomPanelHeight: attachedDatabase.typeMapping.read(
        DriftSqlType.double,
        data['${effectivePrefix}bottom_panel_height'],
      ),
    );
  }

  @override
  $DesktopEditorStateTable createAlias(String alias) {
    return $DesktopEditorStateTable(attachedDatabase, alias);
  }
}

class DesktopEditorStateData extends DataClass
    implements Insertable<DesktopEditorStateData> {
  final String workspaceId;
  final String openTabsJson;
  final String? activeTabPath;
  final double? sidebarWidth;
  final double? agentPanelWidth;
  final double? bottomPanelHeight;
  const DesktopEditorStateData({
    required this.workspaceId,
    required this.openTabsJson,
    this.activeTabPath,
    this.sidebarWidth,
    this.agentPanelWidth,
    this.bottomPanelHeight,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['workspace_id'] = Variable<String>(workspaceId);
    map['open_tabs_json'] = Variable<String>(openTabsJson);
    if (!nullToAbsent || activeTabPath != null) {
      map['active_tab_path'] = Variable<String>(activeTabPath);
    }
    if (!nullToAbsent || sidebarWidth != null) {
      map['sidebar_width'] = Variable<double>(sidebarWidth);
    }
    if (!nullToAbsent || agentPanelWidth != null) {
      map['agent_panel_width'] = Variable<double>(agentPanelWidth);
    }
    if (!nullToAbsent || bottomPanelHeight != null) {
      map['bottom_panel_height'] = Variable<double>(bottomPanelHeight);
    }
    return map;
  }

  DesktopEditorStateCompanion toCompanion(bool nullToAbsent) {
    return DesktopEditorStateCompanion(
      workspaceId: Value(workspaceId),
      openTabsJson: Value(openTabsJson),
      activeTabPath: activeTabPath == null && nullToAbsent
          ? const Value.absent()
          : Value(activeTabPath),
      sidebarWidth: sidebarWidth == null && nullToAbsent
          ? const Value.absent()
          : Value(sidebarWidth),
      agentPanelWidth: agentPanelWidth == null && nullToAbsent
          ? const Value.absent()
          : Value(agentPanelWidth),
      bottomPanelHeight: bottomPanelHeight == null && nullToAbsent
          ? const Value.absent()
          : Value(bottomPanelHeight),
    );
  }

  factory DesktopEditorStateData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DesktopEditorStateData(
      workspaceId: serializer.fromJson<String>(json['workspaceId']),
      openTabsJson: serializer.fromJson<String>(json['openTabsJson']),
      activeTabPath: serializer.fromJson<String?>(json['activeTabPath']),
      sidebarWidth: serializer.fromJson<double?>(json['sidebarWidth']),
      agentPanelWidth: serializer.fromJson<double?>(json['agentPanelWidth']),
      bottomPanelHeight: serializer.fromJson<double?>(
        json['bottomPanelHeight'],
      ),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'workspaceId': serializer.toJson<String>(workspaceId),
      'openTabsJson': serializer.toJson<String>(openTabsJson),
      'activeTabPath': serializer.toJson<String?>(activeTabPath),
      'sidebarWidth': serializer.toJson<double?>(sidebarWidth),
      'agentPanelWidth': serializer.toJson<double?>(agentPanelWidth),
      'bottomPanelHeight': serializer.toJson<double?>(bottomPanelHeight),
    };
  }

  DesktopEditorStateData copyWith({
    String? workspaceId,
    String? openTabsJson,
    Value<String?> activeTabPath = const Value.absent(),
    Value<double?> sidebarWidth = const Value.absent(),
    Value<double?> agentPanelWidth = const Value.absent(),
    Value<double?> bottomPanelHeight = const Value.absent(),
  }) => DesktopEditorStateData(
    workspaceId: workspaceId ?? this.workspaceId,
    openTabsJson: openTabsJson ?? this.openTabsJson,
    activeTabPath: activeTabPath.present
        ? activeTabPath.value
        : this.activeTabPath,
    sidebarWidth: sidebarWidth.present ? sidebarWidth.value : this.sidebarWidth,
    agentPanelWidth: agentPanelWidth.present
        ? agentPanelWidth.value
        : this.agentPanelWidth,
    bottomPanelHeight: bottomPanelHeight.present
        ? bottomPanelHeight.value
        : this.bottomPanelHeight,
  );
  DesktopEditorStateData copyWithCompanion(DesktopEditorStateCompanion data) {
    return DesktopEditorStateData(
      workspaceId: data.workspaceId.present
          ? data.workspaceId.value
          : this.workspaceId,
      openTabsJson: data.openTabsJson.present
          ? data.openTabsJson.value
          : this.openTabsJson,
      activeTabPath: data.activeTabPath.present
          ? data.activeTabPath.value
          : this.activeTabPath,
      sidebarWidth: data.sidebarWidth.present
          ? data.sidebarWidth.value
          : this.sidebarWidth,
      agentPanelWidth: data.agentPanelWidth.present
          ? data.agentPanelWidth.value
          : this.agentPanelWidth,
      bottomPanelHeight: data.bottomPanelHeight.present
          ? data.bottomPanelHeight.value
          : this.bottomPanelHeight,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DesktopEditorStateData(')
          ..write('workspaceId: $workspaceId, ')
          ..write('openTabsJson: $openTabsJson, ')
          ..write('activeTabPath: $activeTabPath, ')
          ..write('sidebarWidth: $sidebarWidth, ')
          ..write('agentPanelWidth: $agentPanelWidth, ')
          ..write('bottomPanelHeight: $bottomPanelHeight')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(
    workspaceId,
    openTabsJson,
    activeTabPath,
    sidebarWidth,
    agentPanelWidth,
    bottomPanelHeight,
  );
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DesktopEditorStateData &&
          other.workspaceId == this.workspaceId &&
          other.openTabsJson == this.openTabsJson &&
          other.activeTabPath == this.activeTabPath &&
          other.sidebarWidth == this.sidebarWidth &&
          other.agentPanelWidth == this.agentPanelWidth &&
          other.bottomPanelHeight == this.bottomPanelHeight);
}

class DesktopEditorStateCompanion
    extends UpdateCompanion<DesktopEditorStateData> {
  final Value<String> workspaceId;
  final Value<String> openTabsJson;
  final Value<String?> activeTabPath;
  final Value<double?> sidebarWidth;
  final Value<double?> agentPanelWidth;
  final Value<double?> bottomPanelHeight;
  final Value<int> rowid;
  const DesktopEditorStateCompanion({
    this.workspaceId = const Value.absent(),
    this.openTabsJson = const Value.absent(),
    this.activeTabPath = const Value.absent(),
    this.sidebarWidth = const Value.absent(),
    this.agentPanelWidth = const Value.absent(),
    this.bottomPanelHeight = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  DesktopEditorStateCompanion.insert({
    required String workspaceId,
    required String openTabsJson,
    this.activeTabPath = const Value.absent(),
    this.sidebarWidth = const Value.absent(),
    this.agentPanelWidth = const Value.absent(),
    this.bottomPanelHeight = const Value.absent(),
    this.rowid = const Value.absent(),
  }) : workspaceId = Value(workspaceId),
       openTabsJson = Value(openTabsJson);
  static Insertable<DesktopEditorStateData> custom({
    Expression<String>? workspaceId,
    Expression<String>? openTabsJson,
    Expression<String>? activeTabPath,
    Expression<double>? sidebarWidth,
    Expression<double>? agentPanelWidth,
    Expression<double>? bottomPanelHeight,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (workspaceId != null) 'workspace_id': workspaceId,
      if (openTabsJson != null) 'open_tabs_json': openTabsJson,
      if (activeTabPath != null) 'active_tab_path': activeTabPath,
      if (sidebarWidth != null) 'sidebar_width': sidebarWidth,
      if (agentPanelWidth != null) 'agent_panel_width': agentPanelWidth,
      if (bottomPanelHeight != null) 'bottom_panel_height': bottomPanelHeight,
      if (rowid != null) 'rowid': rowid,
    });
  }

  DesktopEditorStateCompanion copyWith({
    Value<String>? workspaceId,
    Value<String>? openTabsJson,
    Value<String?>? activeTabPath,
    Value<double?>? sidebarWidth,
    Value<double?>? agentPanelWidth,
    Value<double?>? bottomPanelHeight,
    Value<int>? rowid,
  }) {
    return DesktopEditorStateCompanion(
      workspaceId: workspaceId ?? this.workspaceId,
      openTabsJson: openTabsJson ?? this.openTabsJson,
      activeTabPath: activeTabPath ?? this.activeTabPath,
      sidebarWidth: sidebarWidth ?? this.sidebarWidth,
      agentPanelWidth: agentPanelWidth ?? this.agentPanelWidth,
      bottomPanelHeight: bottomPanelHeight ?? this.bottomPanelHeight,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (workspaceId.present) {
      map['workspace_id'] = Variable<String>(workspaceId.value);
    }
    if (openTabsJson.present) {
      map['open_tabs_json'] = Variable<String>(openTabsJson.value);
    }
    if (activeTabPath.present) {
      map['active_tab_path'] = Variable<String>(activeTabPath.value);
    }
    if (sidebarWidth.present) {
      map['sidebar_width'] = Variable<double>(sidebarWidth.value);
    }
    if (agentPanelWidth.present) {
      map['agent_panel_width'] = Variable<double>(agentPanelWidth.value);
    }
    if (bottomPanelHeight.present) {
      map['bottom_panel_height'] = Variable<double>(bottomPanelHeight.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('DesktopEditorStateCompanion(')
          ..write('workspaceId: $workspaceId, ')
          ..write('openTabsJson: $openTabsJson, ')
          ..write('activeTabPath: $activeTabPath, ')
          ..write('sidebarWidth: $sidebarWidth, ')
          ..write('agentPanelWidth: $agentPanelWidth, ')
          ..write('bottomPanelHeight: $bottomPanelHeight, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $DesktopTerminalStateTable extends DesktopTerminalState
    with TableInfo<$DesktopTerminalStateTable, DesktopTerminalStateData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $DesktopTerminalStateTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _workspaceIdMeta = const VerificationMeta(
    'workspaceId',
  );
  @override
  late final GeneratedColumn<String> workspaceId = GeneratedColumn<String>(
    'workspace_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _sessionsJsonMeta = const VerificationMeta(
    'sessionsJson',
  );
  @override
  late final GeneratedColumn<String> sessionsJson = GeneratedColumn<String>(
    'sessions_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [workspaceId, sessionsJson];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'desktop_terminal_state';
  @override
  VerificationContext validateIntegrity(
    Insertable<DesktopTerminalStateData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('workspace_id')) {
      context.handle(
        _workspaceIdMeta,
        workspaceId.isAcceptableOrUnknown(
          data['workspace_id']!,
          _workspaceIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_workspaceIdMeta);
    }
    if (data.containsKey('sessions_json')) {
      context.handle(
        _sessionsJsonMeta,
        sessionsJson.isAcceptableOrUnknown(
          data['sessions_json']!,
          _sessionsJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_sessionsJsonMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {workspaceId};
  @override
  DesktopTerminalStateData map(
    Map<String, dynamic> data, {
    String? tablePrefix,
  }) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DesktopTerminalStateData(
      workspaceId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}workspace_id'],
      )!,
      sessionsJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}sessions_json'],
      )!,
    );
  }

  @override
  $DesktopTerminalStateTable createAlias(String alias) {
    return $DesktopTerminalStateTable(attachedDatabase, alias);
  }
}

class DesktopTerminalStateData extends DataClass
    implements Insertable<DesktopTerminalStateData> {
  final String workspaceId;
  final String sessionsJson;
  const DesktopTerminalStateData({
    required this.workspaceId,
    required this.sessionsJson,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['workspace_id'] = Variable<String>(workspaceId);
    map['sessions_json'] = Variable<String>(sessionsJson);
    return map;
  }

  DesktopTerminalStateCompanion toCompanion(bool nullToAbsent) {
    return DesktopTerminalStateCompanion(
      workspaceId: Value(workspaceId),
      sessionsJson: Value(sessionsJson),
    );
  }

  factory DesktopTerminalStateData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DesktopTerminalStateData(
      workspaceId: serializer.fromJson<String>(json['workspaceId']),
      sessionsJson: serializer.fromJson<String>(json['sessionsJson']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'workspaceId': serializer.toJson<String>(workspaceId),
      'sessionsJson': serializer.toJson<String>(sessionsJson),
    };
  }

  DesktopTerminalStateData copyWith({
    String? workspaceId,
    String? sessionsJson,
  }) => DesktopTerminalStateData(
    workspaceId: workspaceId ?? this.workspaceId,
    sessionsJson: sessionsJson ?? this.sessionsJson,
  );
  DesktopTerminalStateData copyWithCompanion(
    DesktopTerminalStateCompanion data,
  ) {
    return DesktopTerminalStateData(
      workspaceId: data.workspaceId.present
          ? data.workspaceId.value
          : this.workspaceId,
      sessionsJson: data.sessionsJson.present
          ? data.sessionsJson.value
          : this.sessionsJson,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DesktopTerminalStateData(')
          ..write('workspaceId: $workspaceId, ')
          ..write('sessionsJson: $sessionsJson')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(workspaceId, sessionsJson);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DesktopTerminalStateData &&
          other.workspaceId == this.workspaceId &&
          other.sessionsJson == this.sessionsJson);
}

class DesktopTerminalStateCompanion
    extends UpdateCompanion<DesktopTerminalStateData> {
  final Value<String> workspaceId;
  final Value<String> sessionsJson;
  final Value<int> rowid;
  const DesktopTerminalStateCompanion({
    this.workspaceId = const Value.absent(),
    this.sessionsJson = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  DesktopTerminalStateCompanion.insert({
    required String workspaceId,
    required String sessionsJson,
    this.rowid = const Value.absent(),
  }) : workspaceId = Value(workspaceId),
       sessionsJson = Value(sessionsJson);
  static Insertable<DesktopTerminalStateData> custom({
    Expression<String>? workspaceId,
    Expression<String>? sessionsJson,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (workspaceId != null) 'workspace_id': workspaceId,
      if (sessionsJson != null) 'sessions_json': sessionsJson,
      if (rowid != null) 'rowid': rowid,
    });
  }

  DesktopTerminalStateCompanion copyWith({
    Value<String>? workspaceId,
    Value<String>? sessionsJson,
    Value<int>? rowid,
  }) {
    return DesktopTerminalStateCompanion(
      workspaceId: workspaceId ?? this.workspaceId,
      sessionsJson: sessionsJson ?? this.sessionsJson,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (workspaceId.present) {
      map['workspace_id'] = Variable<String>(workspaceId.value);
    }
    if (sessionsJson.present) {
      map['sessions_json'] = Variable<String>(sessionsJson.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('DesktopTerminalStateCompanion(')
          ..write('workspaceId: $workspaceId, ')
          ..write('sessionsJson: $sessionsJson, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $DesktopLayoutStateTable extends DesktopLayoutState
    with TableInfo<$DesktopLayoutStateTable, DesktopLayoutStateData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $DesktopLayoutStateTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _workspaceIdMeta = const VerificationMeta(
    'workspaceId',
  );
  @override
  late final GeneratedColumn<String> workspaceId = GeneratedColumn<String>(
    'workspace_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _windowGeometryJsonMeta =
      const VerificationMeta('windowGeometryJson');
  @override
  late final GeneratedColumn<String> windowGeometryJson =
      GeneratedColumn<String>(
        'window_geometry_json',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      );
  static const VerificationMeta _panelVisibilityJsonMeta =
      const VerificationMeta('panelVisibilityJson');
  @override
  late final GeneratedColumn<String> panelVisibilityJson =
      GeneratedColumn<String>(
        'panel_visibility_json',
        aliasedName,
        false,
        type: DriftSqlType.string,
        requiredDuringInsert: true,
      );
  @override
  List<GeneratedColumn> get $columns => [
    workspaceId,
    windowGeometryJson,
    panelVisibilityJson,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'desktop_layout_state';
  @override
  VerificationContext validateIntegrity(
    Insertable<DesktopLayoutStateData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('workspace_id')) {
      context.handle(
        _workspaceIdMeta,
        workspaceId.isAcceptableOrUnknown(
          data['workspace_id']!,
          _workspaceIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_workspaceIdMeta);
    }
    if (data.containsKey('window_geometry_json')) {
      context.handle(
        _windowGeometryJsonMeta,
        windowGeometryJson.isAcceptableOrUnknown(
          data['window_geometry_json']!,
          _windowGeometryJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_windowGeometryJsonMeta);
    }
    if (data.containsKey('panel_visibility_json')) {
      context.handle(
        _panelVisibilityJsonMeta,
        panelVisibilityJson.isAcceptableOrUnknown(
          data['panel_visibility_json']!,
          _panelVisibilityJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_panelVisibilityJsonMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {workspaceId};
  @override
  DesktopLayoutStateData map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DesktopLayoutStateData(
      workspaceId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}workspace_id'],
      )!,
      windowGeometryJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}window_geometry_json'],
      )!,
      panelVisibilityJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}panel_visibility_json'],
      )!,
    );
  }

  @override
  $DesktopLayoutStateTable createAlias(String alias) {
    return $DesktopLayoutStateTable(attachedDatabase, alias);
  }
}

class DesktopLayoutStateData extends DataClass
    implements Insertable<DesktopLayoutStateData> {
  final String workspaceId;
  final String windowGeometryJson;
  final String panelVisibilityJson;
  const DesktopLayoutStateData({
    required this.workspaceId,
    required this.windowGeometryJson,
    required this.panelVisibilityJson,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['workspace_id'] = Variable<String>(workspaceId);
    map['window_geometry_json'] = Variable<String>(windowGeometryJson);
    map['panel_visibility_json'] = Variable<String>(panelVisibilityJson);
    return map;
  }

  DesktopLayoutStateCompanion toCompanion(bool nullToAbsent) {
    return DesktopLayoutStateCompanion(
      workspaceId: Value(workspaceId),
      windowGeometryJson: Value(windowGeometryJson),
      panelVisibilityJson: Value(panelVisibilityJson),
    );
  }

  factory DesktopLayoutStateData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DesktopLayoutStateData(
      workspaceId: serializer.fromJson<String>(json['workspaceId']),
      windowGeometryJson: serializer.fromJson<String>(
        json['windowGeometryJson'],
      ),
      panelVisibilityJson: serializer.fromJson<String>(
        json['panelVisibilityJson'],
      ),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'workspaceId': serializer.toJson<String>(workspaceId),
      'windowGeometryJson': serializer.toJson<String>(windowGeometryJson),
      'panelVisibilityJson': serializer.toJson<String>(panelVisibilityJson),
    };
  }

  DesktopLayoutStateData copyWith({
    String? workspaceId,
    String? windowGeometryJson,
    String? panelVisibilityJson,
  }) => DesktopLayoutStateData(
    workspaceId: workspaceId ?? this.workspaceId,
    windowGeometryJson: windowGeometryJson ?? this.windowGeometryJson,
    panelVisibilityJson: panelVisibilityJson ?? this.panelVisibilityJson,
  );
  DesktopLayoutStateData copyWithCompanion(DesktopLayoutStateCompanion data) {
    return DesktopLayoutStateData(
      workspaceId: data.workspaceId.present
          ? data.workspaceId.value
          : this.workspaceId,
      windowGeometryJson: data.windowGeometryJson.present
          ? data.windowGeometryJson.value
          : this.windowGeometryJson,
      panelVisibilityJson: data.panelVisibilityJson.present
          ? data.panelVisibilityJson.value
          : this.panelVisibilityJson,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DesktopLayoutStateData(')
          ..write('workspaceId: $workspaceId, ')
          ..write('windowGeometryJson: $windowGeometryJson, ')
          ..write('panelVisibilityJson: $panelVisibilityJson')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode =>
      Object.hash(workspaceId, windowGeometryJson, panelVisibilityJson);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DesktopLayoutStateData &&
          other.workspaceId == this.workspaceId &&
          other.windowGeometryJson == this.windowGeometryJson &&
          other.panelVisibilityJson == this.panelVisibilityJson);
}

class DesktopLayoutStateCompanion
    extends UpdateCompanion<DesktopLayoutStateData> {
  final Value<String> workspaceId;
  final Value<String> windowGeometryJson;
  final Value<String> panelVisibilityJson;
  final Value<int> rowid;
  const DesktopLayoutStateCompanion({
    this.workspaceId = const Value.absent(),
    this.windowGeometryJson = const Value.absent(),
    this.panelVisibilityJson = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  DesktopLayoutStateCompanion.insert({
    required String workspaceId,
    required String windowGeometryJson,
    required String panelVisibilityJson,
    this.rowid = const Value.absent(),
  }) : workspaceId = Value(workspaceId),
       windowGeometryJson = Value(windowGeometryJson),
       panelVisibilityJson = Value(panelVisibilityJson);
  static Insertable<DesktopLayoutStateData> custom({
    Expression<String>? workspaceId,
    Expression<String>? windowGeometryJson,
    Expression<String>? panelVisibilityJson,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (workspaceId != null) 'workspace_id': workspaceId,
      if (windowGeometryJson != null)
        'window_geometry_json': windowGeometryJson,
      if (panelVisibilityJson != null)
        'panel_visibility_json': panelVisibilityJson,
      if (rowid != null) 'rowid': rowid,
    });
  }

  DesktopLayoutStateCompanion copyWith({
    Value<String>? workspaceId,
    Value<String>? windowGeometryJson,
    Value<String>? panelVisibilityJson,
    Value<int>? rowid,
  }) {
    return DesktopLayoutStateCompanion(
      workspaceId: workspaceId ?? this.workspaceId,
      windowGeometryJson: windowGeometryJson ?? this.windowGeometryJson,
      panelVisibilityJson: panelVisibilityJson ?? this.panelVisibilityJson,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (workspaceId.present) {
      map['workspace_id'] = Variable<String>(workspaceId.value);
    }
    if (windowGeometryJson.present) {
      map['window_geometry_json'] = Variable<String>(windowGeometryJson.value);
    }
    if (panelVisibilityJson.present) {
      map['panel_visibility_json'] = Variable<String>(
        panelVisibilityJson.value,
      );
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('DesktopLayoutStateCompanion(')
          ..write('workspaceId: $workspaceId, ')
          ..write('windowGeometryJson: $windowGeometryJson, ')
          ..write('panelVisibilityJson: $panelVisibilityJson, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $DesktopAgentStateTable extends DesktopAgentState
    with TableInfo<$DesktopAgentStateTable, DesktopAgentStateData> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $DesktopAgentStateTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _workspaceIdMeta = const VerificationMeta(
    'workspaceId',
  );
  @override
  late final GeneratedColumn<String> workspaceId = GeneratedColumn<String>(
    'workspace_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _agentStateJsonMeta = const VerificationMeta(
    'agentStateJson',
  );
  @override
  late final GeneratedColumn<String> agentStateJson = GeneratedColumn<String>(
    'agent_state_json',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [workspaceId, agentStateJson];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'desktop_agent_state';
  @override
  VerificationContext validateIntegrity(
    Insertable<DesktopAgentStateData> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('workspace_id')) {
      context.handle(
        _workspaceIdMeta,
        workspaceId.isAcceptableOrUnknown(
          data['workspace_id']!,
          _workspaceIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_workspaceIdMeta);
    }
    if (data.containsKey('agent_state_json')) {
      context.handle(
        _agentStateJsonMeta,
        agentStateJson.isAcceptableOrUnknown(
          data['agent_state_json']!,
          _agentStateJsonMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_agentStateJsonMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {workspaceId};
  @override
  DesktopAgentStateData map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DesktopAgentStateData(
      workspaceId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}workspace_id'],
      )!,
      agentStateJson: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}agent_state_json'],
      )!,
    );
  }

  @override
  $DesktopAgentStateTable createAlias(String alias) {
    return $DesktopAgentStateTable(attachedDatabase, alias);
  }
}

class DesktopAgentStateData extends DataClass
    implements Insertable<DesktopAgentStateData> {
  final String workspaceId;
  final String agentStateJson;
  const DesktopAgentStateData({
    required this.workspaceId,
    required this.agentStateJson,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['workspace_id'] = Variable<String>(workspaceId);
    map['agent_state_json'] = Variable<String>(agentStateJson);
    return map;
  }

  DesktopAgentStateCompanion toCompanion(bool nullToAbsent) {
    return DesktopAgentStateCompanion(
      workspaceId: Value(workspaceId),
      agentStateJson: Value(agentStateJson),
    );
  }

  factory DesktopAgentStateData.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DesktopAgentStateData(
      workspaceId: serializer.fromJson<String>(json['workspaceId']),
      agentStateJson: serializer.fromJson<String>(json['agentStateJson']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'workspaceId': serializer.toJson<String>(workspaceId),
      'agentStateJson': serializer.toJson<String>(agentStateJson),
    };
  }

  DesktopAgentStateData copyWith({
    String? workspaceId,
    String? agentStateJson,
  }) => DesktopAgentStateData(
    workspaceId: workspaceId ?? this.workspaceId,
    agentStateJson: agentStateJson ?? this.agentStateJson,
  );
  DesktopAgentStateData copyWithCompanion(DesktopAgentStateCompanion data) {
    return DesktopAgentStateData(
      workspaceId: data.workspaceId.present
          ? data.workspaceId.value
          : this.workspaceId,
      agentStateJson: data.agentStateJson.present
          ? data.agentStateJson.value
          : this.agentStateJson,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DesktopAgentStateData(')
          ..write('workspaceId: $workspaceId, ')
          ..write('agentStateJson: $agentStateJson')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode => Object.hash(workspaceId, agentStateJson);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DesktopAgentStateData &&
          other.workspaceId == this.workspaceId &&
          other.agentStateJson == this.agentStateJson);
}

class DesktopAgentStateCompanion
    extends UpdateCompanion<DesktopAgentStateData> {
  final Value<String> workspaceId;
  final Value<String> agentStateJson;
  final Value<int> rowid;
  const DesktopAgentStateCompanion({
    this.workspaceId = const Value.absent(),
    this.agentStateJson = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  DesktopAgentStateCompanion.insert({
    required String workspaceId,
    required String agentStateJson,
    this.rowid = const Value.absent(),
  }) : workspaceId = Value(workspaceId),
       agentStateJson = Value(agentStateJson);
  static Insertable<DesktopAgentStateData> custom({
    Expression<String>? workspaceId,
    Expression<String>? agentStateJson,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (workspaceId != null) 'workspace_id': workspaceId,
      if (agentStateJson != null) 'agent_state_json': agentStateJson,
      if (rowid != null) 'rowid': rowid,
    });
  }

  DesktopAgentStateCompanion copyWith({
    Value<String>? workspaceId,
    Value<String>? agentStateJson,
    Value<int>? rowid,
  }) {
    return DesktopAgentStateCompanion(
      workspaceId: workspaceId ?? this.workspaceId,
      agentStateJson: agentStateJson ?? this.agentStateJson,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (workspaceId.present) {
      map['workspace_id'] = Variable<String>(workspaceId.value);
    }
    if (agentStateJson.present) {
      map['agent_state_json'] = Variable<String>(agentStateJson.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('DesktopAgentStateCompanion(')
          ..write('workspaceId: $workspaceId, ')
          ..write('agentStateJson: $agentStateJson, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

class $DesktopUnsavedBuffersTable extends DesktopUnsavedBuffers
    with TableInfo<$DesktopUnsavedBuffersTable, DesktopUnsavedBuffer> {
  @override
  final GeneratedDatabase attachedDatabase;
  final String? _alias;
  $DesktopUnsavedBuffersTable(this.attachedDatabase, [this._alias]);
  static const VerificationMeta _workspaceIdMeta = const VerificationMeta(
    'workspaceId',
  );
  @override
  late final GeneratedColumn<String> workspaceId = GeneratedColumn<String>(
    'workspace_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _tabIdMeta = const VerificationMeta('tabId');
  @override
  late final GeneratedColumn<String> tabId = GeneratedColumn<String>(
    'tab_id',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _pathMeta = const VerificationMeta('path');
  @override
  late final GeneratedColumn<String> path = GeneratedColumn<String>(
    'path',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _contentEncMeta = const VerificationMeta(
    'contentEnc',
  );
  @override
  late final GeneratedColumn<String> contentEnc = GeneratedColumn<String>(
    'content_enc',
    aliasedName,
    false,
    type: DriftSqlType.string,
    requiredDuringInsert: true,
  );
  static const VerificationMeta _baseHashMeta = const VerificationMeta(
    'baseHash',
  );
  @override
  late final GeneratedColumn<String> baseHash = GeneratedColumn<String>(
    'base_hash',
    aliasedName,
    true,
    type: DriftSqlType.string,
    requiredDuringInsert: false,
  );
  static const VerificationMeta _updatedAtMeta = const VerificationMeta(
    'updatedAt',
  );
  @override
  late final GeneratedColumn<DateTime> updatedAt = GeneratedColumn<DateTime>(
    'updated_at',
    aliasedName,
    false,
    type: DriftSqlType.dateTime,
    requiredDuringInsert: true,
  );
  @override
  List<GeneratedColumn> get $columns => [
    workspaceId,
    tabId,
    path,
    contentEnc,
    baseHash,
    updatedAt,
  ];
  @override
  String get aliasedName => _alias ?? actualTableName;
  @override
  String get actualTableName => $name;
  static const String $name = 'desktop_unsaved_buffers';
  @override
  VerificationContext validateIntegrity(
    Insertable<DesktopUnsavedBuffer> instance, {
    bool isInserting = false,
  }) {
    final context = VerificationContext();
    final data = instance.toColumns(true);
    if (data.containsKey('workspace_id')) {
      context.handle(
        _workspaceIdMeta,
        workspaceId.isAcceptableOrUnknown(
          data['workspace_id']!,
          _workspaceIdMeta,
        ),
      );
    } else if (isInserting) {
      context.missing(_workspaceIdMeta);
    }
    if (data.containsKey('tab_id')) {
      context.handle(
        _tabIdMeta,
        tabId.isAcceptableOrUnknown(data['tab_id']!, _tabIdMeta),
      );
    } else if (isInserting) {
      context.missing(_tabIdMeta);
    }
    if (data.containsKey('path')) {
      context.handle(
        _pathMeta,
        path.isAcceptableOrUnknown(data['path']!, _pathMeta),
      );
    } else if (isInserting) {
      context.missing(_pathMeta);
    }
    if (data.containsKey('content_enc')) {
      context.handle(
        _contentEncMeta,
        contentEnc.isAcceptableOrUnknown(data['content_enc']!, _contentEncMeta),
      );
    } else if (isInserting) {
      context.missing(_contentEncMeta);
    }
    if (data.containsKey('base_hash')) {
      context.handle(
        _baseHashMeta,
        baseHash.isAcceptableOrUnknown(data['base_hash']!, _baseHashMeta),
      );
    }
    if (data.containsKey('updated_at')) {
      context.handle(
        _updatedAtMeta,
        updatedAt.isAcceptableOrUnknown(data['updated_at']!, _updatedAtMeta),
      );
    } else if (isInserting) {
      context.missing(_updatedAtMeta);
    }
    return context;
  }

  @override
  Set<GeneratedColumn> get $primaryKey => {workspaceId, tabId};
  @override
  DesktopUnsavedBuffer map(Map<String, dynamic> data, {String? tablePrefix}) {
    final effectivePrefix = tablePrefix != null ? '$tablePrefix.' : '';
    return DesktopUnsavedBuffer(
      workspaceId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}workspace_id'],
      )!,
      tabId: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}tab_id'],
      )!,
      path: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}path'],
      )!,
      contentEnc: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}content_enc'],
      )!,
      baseHash: attachedDatabase.typeMapping.read(
        DriftSqlType.string,
        data['${effectivePrefix}base_hash'],
      ),
      updatedAt: attachedDatabase.typeMapping.read(
        DriftSqlType.dateTime,
        data['${effectivePrefix}updated_at'],
      )!,
    );
  }

  @override
  $DesktopUnsavedBuffersTable createAlias(String alias) {
    return $DesktopUnsavedBuffersTable(attachedDatabase, alias);
  }
}

class DesktopUnsavedBuffer extends DataClass
    implements Insertable<DesktopUnsavedBuffer> {
  final String workspaceId;
  final String tabId;
  final String path;
  final String contentEnc;
  final String? baseHash;
  final DateTime updatedAt;
  const DesktopUnsavedBuffer({
    required this.workspaceId,
    required this.tabId,
    required this.path,
    required this.contentEnc,
    this.baseHash,
    required this.updatedAt,
  });
  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    map['workspace_id'] = Variable<String>(workspaceId);
    map['tab_id'] = Variable<String>(tabId);
    map['path'] = Variable<String>(path);
    map['content_enc'] = Variable<String>(contentEnc);
    if (!nullToAbsent || baseHash != null) {
      map['base_hash'] = Variable<String>(baseHash);
    }
    map['updated_at'] = Variable<DateTime>(updatedAt);
    return map;
  }

  DesktopUnsavedBuffersCompanion toCompanion(bool nullToAbsent) {
    return DesktopUnsavedBuffersCompanion(
      workspaceId: Value(workspaceId),
      tabId: Value(tabId),
      path: Value(path),
      contentEnc: Value(contentEnc),
      baseHash: baseHash == null && nullToAbsent
          ? const Value.absent()
          : Value(baseHash),
      updatedAt: Value(updatedAt),
    );
  }

  factory DesktopUnsavedBuffer.fromJson(
    Map<String, dynamic> json, {
    ValueSerializer? serializer,
  }) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return DesktopUnsavedBuffer(
      workspaceId: serializer.fromJson<String>(json['workspaceId']),
      tabId: serializer.fromJson<String>(json['tabId']),
      path: serializer.fromJson<String>(json['path']),
      contentEnc: serializer.fromJson<String>(json['contentEnc']),
      baseHash: serializer.fromJson<String?>(json['baseHash']),
      updatedAt: serializer.fromJson<DateTime>(json['updatedAt']),
    );
  }
  @override
  Map<String, dynamic> toJson({ValueSerializer? serializer}) {
    serializer ??= driftRuntimeOptions.defaultSerializer;
    return <String, dynamic>{
      'workspaceId': serializer.toJson<String>(workspaceId),
      'tabId': serializer.toJson<String>(tabId),
      'path': serializer.toJson<String>(path),
      'contentEnc': serializer.toJson<String>(contentEnc),
      'baseHash': serializer.toJson<String?>(baseHash),
      'updatedAt': serializer.toJson<DateTime>(updatedAt),
    };
  }

  DesktopUnsavedBuffer copyWith({
    String? workspaceId,
    String? tabId,
    String? path,
    String? contentEnc,
    Value<String?> baseHash = const Value.absent(),
    DateTime? updatedAt,
  }) => DesktopUnsavedBuffer(
    workspaceId: workspaceId ?? this.workspaceId,
    tabId: tabId ?? this.tabId,
    path: path ?? this.path,
    contentEnc: contentEnc ?? this.contentEnc,
    baseHash: baseHash.present ? baseHash.value : this.baseHash,
    updatedAt: updatedAt ?? this.updatedAt,
  );
  DesktopUnsavedBuffer copyWithCompanion(DesktopUnsavedBuffersCompanion data) {
    return DesktopUnsavedBuffer(
      workspaceId: data.workspaceId.present
          ? data.workspaceId.value
          : this.workspaceId,
      tabId: data.tabId.present ? data.tabId.value : this.tabId,
      path: data.path.present ? data.path.value : this.path,
      contentEnc: data.contentEnc.present
          ? data.contentEnc.value
          : this.contentEnc,
      baseHash: data.baseHash.present ? data.baseHash.value : this.baseHash,
      updatedAt: data.updatedAt.present ? data.updatedAt.value : this.updatedAt,
    );
  }

  @override
  String toString() {
    return (StringBuffer('DesktopUnsavedBuffer(')
          ..write('workspaceId: $workspaceId, ')
          ..write('tabId: $tabId, ')
          ..write('path: $path, ')
          ..write('contentEnc: $contentEnc, ')
          ..write('baseHash: $baseHash, ')
          ..write('updatedAt: $updatedAt')
          ..write(')'))
        .toString();
  }

  @override
  int get hashCode =>
      Object.hash(workspaceId, tabId, path, contentEnc, baseHash, updatedAt);
  @override
  bool operator ==(Object other) =>
      identical(this, other) ||
      (other is DesktopUnsavedBuffer &&
          other.workspaceId == this.workspaceId &&
          other.tabId == this.tabId &&
          other.path == this.path &&
          other.contentEnc == this.contentEnc &&
          other.baseHash == this.baseHash &&
          other.updatedAt == this.updatedAt);
}

class DesktopUnsavedBuffersCompanion
    extends UpdateCompanion<DesktopUnsavedBuffer> {
  final Value<String> workspaceId;
  final Value<String> tabId;
  final Value<String> path;
  final Value<String> contentEnc;
  final Value<String?> baseHash;
  final Value<DateTime> updatedAt;
  final Value<int> rowid;
  const DesktopUnsavedBuffersCompanion({
    this.workspaceId = const Value.absent(),
    this.tabId = const Value.absent(),
    this.path = const Value.absent(),
    this.contentEnc = const Value.absent(),
    this.baseHash = const Value.absent(),
    this.updatedAt = const Value.absent(),
    this.rowid = const Value.absent(),
  });
  DesktopUnsavedBuffersCompanion.insert({
    required String workspaceId,
    required String tabId,
    required String path,
    required String contentEnc,
    this.baseHash = const Value.absent(),
    required DateTime updatedAt,
    this.rowid = const Value.absent(),
  }) : workspaceId = Value(workspaceId),
       tabId = Value(tabId),
       path = Value(path),
       contentEnc = Value(contentEnc),
       updatedAt = Value(updatedAt);
  static Insertable<DesktopUnsavedBuffer> custom({
    Expression<String>? workspaceId,
    Expression<String>? tabId,
    Expression<String>? path,
    Expression<String>? contentEnc,
    Expression<String>? baseHash,
    Expression<DateTime>? updatedAt,
    Expression<int>? rowid,
  }) {
    return RawValuesInsertable({
      if (workspaceId != null) 'workspace_id': workspaceId,
      if (tabId != null) 'tab_id': tabId,
      if (path != null) 'path': path,
      if (contentEnc != null) 'content_enc': contentEnc,
      if (baseHash != null) 'base_hash': baseHash,
      if (updatedAt != null) 'updated_at': updatedAt,
      if (rowid != null) 'rowid': rowid,
    });
  }

  DesktopUnsavedBuffersCompanion copyWith({
    Value<String>? workspaceId,
    Value<String>? tabId,
    Value<String>? path,
    Value<String>? contentEnc,
    Value<String?>? baseHash,
    Value<DateTime>? updatedAt,
    Value<int>? rowid,
  }) {
    return DesktopUnsavedBuffersCompanion(
      workspaceId: workspaceId ?? this.workspaceId,
      tabId: tabId ?? this.tabId,
      path: path ?? this.path,
      contentEnc: contentEnc ?? this.contentEnc,
      baseHash: baseHash ?? this.baseHash,
      updatedAt: updatedAt ?? this.updatedAt,
      rowid: rowid ?? this.rowid,
    );
  }

  @override
  Map<String, Expression> toColumns(bool nullToAbsent) {
    final map = <String, Expression>{};
    if (workspaceId.present) {
      map['workspace_id'] = Variable<String>(workspaceId.value);
    }
    if (tabId.present) {
      map['tab_id'] = Variable<String>(tabId.value);
    }
    if (path.present) {
      map['path'] = Variable<String>(path.value);
    }
    if (contentEnc.present) {
      map['content_enc'] = Variable<String>(contentEnc.value);
    }
    if (baseHash.present) {
      map['base_hash'] = Variable<String>(baseHash.value);
    }
    if (updatedAt.present) {
      map['updated_at'] = Variable<DateTime>(updatedAt.value);
    }
    if (rowid.present) {
      map['rowid'] = Variable<int>(rowid.value);
    }
    return map;
  }

  @override
  String toString() {
    return (StringBuffer('DesktopUnsavedBuffersCompanion(')
          ..write('workspaceId: $workspaceId, ')
          ..write('tabId: $tabId, ')
          ..write('path: $path, ')
          ..write('contentEnc: $contentEnc, ')
          ..write('baseHash: $baseHash, ')
          ..write('updatedAt: $updatedAt, ')
          ..write('rowid: $rowid')
          ..write(')'))
        .toString();
  }
}

abstract class _$AppDatabase extends GeneratedDatabase {
  _$AppDatabase(QueryExecutor e) : super(e);
  $AppDatabaseManager get managers => $AppDatabaseManager(this);
  late final $TasksTable tasks = $TasksTable(this);
  late final $CalendarEventsTable calendarEvents = $CalendarEventsTable(this);
  late final $FinanceRecordsTable financeRecords = $FinanceRecordsTable(this);
  late final $HealthRecordsTable healthRecords = $HealthRecordsTable(this);
  late final $MessagesTable messages = $MessagesTable(this);
  late final $AgentConfigurationsTable agentConfigurations =
      $AgentConfigurationsTable(this);
  late final $UserMemoriesTable userMemories = $UserMemoriesTable(this);
  late final $ProjectsTable projects = $ProjectsTable(this);
  late final $GoalsTable goals = $GoalsTable(this);
  late final $PendingDeletesTable pendingDeletes = $PendingDeletesTable(this);
  late final $LearningLogsTable learningLogs = $LearningLogsTable(this);
  late final $OfflineModelWeightsTable offlineModelWeights =
      $OfflineModelWeightsTable(this);
  late final $TelemetryLogsTable telemetryLogs = $TelemetryLogsTable(this);
  late final $OutboxEntriesTable outboxEntries = $OutboxEntriesTable(this);
  late final $SyncMetadataTable syncMetadata = $SyncMetadataTable(this);
  late final $DesktopWorkspacesTable desktopWorkspaces =
      $DesktopWorkspacesTable(this);
  late final $DesktopEditorStateTable desktopEditorState =
      $DesktopEditorStateTable(this);
  late final $DesktopTerminalStateTable desktopTerminalState =
      $DesktopTerminalStateTable(this);
  late final $DesktopLayoutStateTable desktopLayoutState =
      $DesktopLayoutStateTable(this);
  late final $DesktopAgentStateTable desktopAgentState =
      $DesktopAgentStateTable(this);
  late final $DesktopUnsavedBuffersTable desktopUnsavedBuffers =
      $DesktopUnsavedBuffersTable(this);
  @override
  Iterable<TableInfo<Table, Object?>> get allTables =>
      allSchemaEntities.whereType<TableInfo<Table, Object?>>();
  @override
  List<DatabaseSchemaEntity> get allSchemaEntities => [
    tasks,
    calendarEvents,
    financeRecords,
    healthRecords,
    messages,
    agentConfigurations,
    userMemories,
    projects,
    goals,
    pendingDeletes,
    learningLogs,
    offlineModelWeights,
    telemetryLogs,
    outboxEntries,
    syncMetadata,
    desktopWorkspaces,
    desktopEditorState,
    desktopTerminalState,
    desktopLayoutState,
    desktopAgentState,
    desktopUnsavedBuffers,
  ];
}

typedef $$TasksTableCreateCompanionBuilder =
    TasksCompanion Function({
      required String id,
      required String title,
      Value<String?> description,
      required String priority,
      Value<DateTime?> dueDate,
      required bool isCompleted,
      Value<String?> googleTaskId,
      required String category,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$TasksTableUpdateCompanionBuilder =
    TasksCompanion Function({
      Value<String> id,
      Value<String> title,
      Value<String?> description,
      Value<String> priority,
      Value<DateTime?> dueDate,
      Value<bool> isCompleted,
      Value<String?> googleTaskId,
      Value<String> category,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$TasksTableFilterComposer extends Composer<_$AppDatabase, $TasksTable> {
  $$TasksTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get title =>
      $composableBuilder(
        column: $table.title,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnWithTypeConverterFilters<String?, String, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<String> get priority => $composableBuilder(
    column: $table.priority,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get dueDate => $composableBuilder(
    column: $table.dueDate,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isCompleted => $composableBuilder(
    column: $table.isCompleted,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get googleTaskId => $composableBuilder(
    column: $table.googleTaskId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get category => $composableBuilder(
    column: $table.category,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$TasksTableOrderingComposer
    extends Composer<_$AppDatabase, $TasksTable> {
  $$TasksTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get title => $composableBuilder(
    column: $table.title,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get description => $composableBuilder(
    column: $table.description,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get priority => $composableBuilder(
    column: $table.priority,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get dueDate => $composableBuilder(
    column: $table.dueDate,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isCompleted => $composableBuilder(
    column: $table.isCompleted,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get googleTaskId => $composableBuilder(
    column: $table.googleTaskId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get category => $composableBuilder(
    column: $table.category,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$TasksTableAnnotationComposer
    extends Composer<_$AppDatabase, $TasksTable> {
  $$TasksTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get title =>
      $composableBuilder(column: $table.title, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => column,
      );

  GeneratedColumn<String> get priority =>
      $composableBuilder(column: $table.priority, builder: (column) => column);

  GeneratedColumn<DateTime> get dueDate =>
      $composableBuilder(column: $table.dueDate, builder: (column) => column);

  GeneratedColumn<bool> get isCompleted => $composableBuilder(
    column: $table.isCompleted,
    builder: (column) => column,
  );

  GeneratedColumn<String> get googleTaskId => $composableBuilder(
    column: $table.googleTaskId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get category =>
      $composableBuilder(column: $table.category, builder: (column) => column);

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$TasksTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $TasksTable,
          Task,
          $$TasksTableFilterComposer,
          $$TasksTableOrderingComposer,
          $$TasksTableAnnotationComposer,
          $$TasksTableCreateCompanionBuilder,
          $$TasksTableUpdateCompanionBuilder,
          (Task, BaseReferences<_$AppDatabase, $TasksTable, Task>),
          Task,
          PrefetchHooks Function()
        > {
  $$TasksTableTableManager(_$AppDatabase db, $TasksTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$TasksTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$TasksTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$TasksTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> title = const Value.absent(),
                Value<String?> description = const Value.absent(),
                Value<String> priority = const Value.absent(),
                Value<DateTime?> dueDate = const Value.absent(),
                Value<bool> isCompleted = const Value.absent(),
                Value<String?> googleTaskId = const Value.absent(),
                Value<String> category = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => TasksCompanion(
                id: id,
                title: title,
                description: description,
                priority: priority,
                dueDate: dueDate,
                isCompleted: isCompleted,
                googleTaskId: googleTaskId,
                category: category,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String title,
                Value<String?> description = const Value.absent(),
                required String priority,
                Value<DateTime?> dueDate = const Value.absent(),
                required bool isCompleted,
                Value<String?> googleTaskId = const Value.absent(),
                required String category,
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => TasksCompanion.insert(
                id: id,
                title: title,
                description: description,
                priority: priority,
                dueDate: dueDate,
                isCompleted: isCompleted,
                googleTaskId: googleTaskId,
                category: category,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$TasksTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $TasksTable,
      Task,
      $$TasksTableFilterComposer,
      $$TasksTableOrderingComposer,
      $$TasksTableAnnotationComposer,
      $$TasksTableCreateCompanionBuilder,
      $$TasksTableUpdateCompanionBuilder,
      (Task, BaseReferences<_$AppDatabase, $TasksTable, Task>),
      Task,
      PrefetchHooks Function()
    >;
typedef $$CalendarEventsTableCreateCompanionBuilder =
    CalendarEventsCompanion Function({
      required String id,
      required String title,
      Value<String?> description,
      required DateTime startTime,
      required DateTime endTime,
      Value<String?> googleEventId,
      required bool isAllDay,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$CalendarEventsTableUpdateCompanionBuilder =
    CalendarEventsCompanion Function({
      Value<String> id,
      Value<String> title,
      Value<String?> description,
      Value<DateTime> startTime,
      Value<DateTime> endTime,
      Value<String?> googleEventId,
      Value<bool> isAllDay,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$CalendarEventsTableFilterComposer
    extends Composer<_$AppDatabase, $CalendarEventsTable> {
  $$CalendarEventsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get title =>
      $composableBuilder(
        column: $table.title,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnWithTypeConverterFilters<String?, String, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<DateTime> get startTime => $composableBuilder(
    column: $table.startTime,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get endTime => $composableBuilder(
    column: $table.endTime,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get googleEventId => $composableBuilder(
    column: $table.googleEventId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isAllDay => $composableBuilder(
    column: $table.isAllDay,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$CalendarEventsTableOrderingComposer
    extends Composer<_$AppDatabase, $CalendarEventsTable> {
  $$CalendarEventsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get title => $composableBuilder(
    column: $table.title,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get description => $composableBuilder(
    column: $table.description,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get startTime => $composableBuilder(
    column: $table.startTime,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get endTime => $composableBuilder(
    column: $table.endTime,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get googleEventId => $composableBuilder(
    column: $table.googleEventId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isAllDay => $composableBuilder(
    column: $table.isAllDay,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$CalendarEventsTableAnnotationComposer
    extends Composer<_$AppDatabase, $CalendarEventsTable> {
  $$CalendarEventsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get title =>
      $composableBuilder(column: $table.title, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => column,
      );

  GeneratedColumn<DateTime> get startTime =>
      $composableBuilder(column: $table.startTime, builder: (column) => column);

  GeneratedColumn<DateTime> get endTime =>
      $composableBuilder(column: $table.endTime, builder: (column) => column);

  GeneratedColumn<String> get googleEventId => $composableBuilder(
    column: $table.googleEventId,
    builder: (column) => column,
  );

  GeneratedColumn<bool> get isAllDay =>
      $composableBuilder(column: $table.isAllDay, builder: (column) => column);

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$CalendarEventsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $CalendarEventsTable,
          CalendarEvent,
          $$CalendarEventsTableFilterComposer,
          $$CalendarEventsTableOrderingComposer,
          $$CalendarEventsTableAnnotationComposer,
          $$CalendarEventsTableCreateCompanionBuilder,
          $$CalendarEventsTableUpdateCompanionBuilder,
          (
            CalendarEvent,
            BaseReferences<_$AppDatabase, $CalendarEventsTable, CalendarEvent>,
          ),
          CalendarEvent,
          PrefetchHooks Function()
        > {
  $$CalendarEventsTableTableManager(
    _$AppDatabase db,
    $CalendarEventsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$CalendarEventsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$CalendarEventsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$CalendarEventsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> title = const Value.absent(),
                Value<String?> description = const Value.absent(),
                Value<DateTime> startTime = const Value.absent(),
                Value<DateTime> endTime = const Value.absent(),
                Value<String?> googleEventId = const Value.absent(),
                Value<bool> isAllDay = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => CalendarEventsCompanion(
                id: id,
                title: title,
                description: description,
                startTime: startTime,
                endTime: endTime,
                googleEventId: googleEventId,
                isAllDay: isAllDay,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String title,
                Value<String?> description = const Value.absent(),
                required DateTime startTime,
                required DateTime endTime,
                Value<String?> googleEventId = const Value.absent(),
                required bool isAllDay,
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => CalendarEventsCompanion.insert(
                id: id,
                title: title,
                description: description,
                startTime: startTime,
                endTime: endTime,
                googleEventId: googleEventId,
                isAllDay: isAllDay,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$CalendarEventsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $CalendarEventsTable,
      CalendarEvent,
      $$CalendarEventsTableFilterComposer,
      $$CalendarEventsTableOrderingComposer,
      $$CalendarEventsTableAnnotationComposer,
      $$CalendarEventsTableCreateCompanionBuilder,
      $$CalendarEventsTableUpdateCompanionBuilder,
      (
        CalendarEvent,
        BaseReferences<_$AppDatabase, $CalendarEventsTable, CalendarEvent>,
      ),
      CalendarEvent,
      PrefetchHooks Function()
    >;
typedef $$FinanceRecordsTableCreateCompanionBuilder =
    FinanceRecordsCompanion Function({
      required String id,
      required String type,
      required double amount,
      required String category,
      Value<String?> description,
      required DateTime date,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$FinanceRecordsTableUpdateCompanionBuilder =
    FinanceRecordsCompanion Function({
      Value<String> id,
      Value<String> type,
      Value<double> amount,
      Value<String> category,
      Value<String?> description,
      Value<DateTime> date,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$FinanceRecordsTableFilterComposer
    extends Composer<_$AppDatabase, $FinanceRecordsTable> {
  $$FinanceRecordsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get type => $composableBuilder(
    column: $table.type,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get amount => $composableBuilder(
    column: $table.amount,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get category => $composableBuilder(
    column: $table.category,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String?, String, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<DateTime> get date => $composableBuilder(
    column: $table.date,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$FinanceRecordsTableOrderingComposer
    extends Composer<_$AppDatabase, $FinanceRecordsTable> {
  $$FinanceRecordsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get type => $composableBuilder(
    column: $table.type,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get amount => $composableBuilder(
    column: $table.amount,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get category => $composableBuilder(
    column: $table.category,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get description => $composableBuilder(
    column: $table.description,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get date => $composableBuilder(
    column: $table.date,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$FinanceRecordsTableAnnotationComposer
    extends Composer<_$AppDatabase, $FinanceRecordsTable> {
  $$FinanceRecordsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get type =>
      $composableBuilder(column: $table.type, builder: (column) => column);

  GeneratedColumn<double> get amount =>
      $composableBuilder(column: $table.amount, builder: (column) => column);

  GeneratedColumn<String> get category =>
      $composableBuilder(column: $table.category, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => column,
      );

  GeneratedColumn<DateTime> get date =>
      $composableBuilder(column: $table.date, builder: (column) => column);

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$FinanceRecordsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $FinanceRecordsTable,
          FinanceRecord,
          $$FinanceRecordsTableFilterComposer,
          $$FinanceRecordsTableOrderingComposer,
          $$FinanceRecordsTableAnnotationComposer,
          $$FinanceRecordsTableCreateCompanionBuilder,
          $$FinanceRecordsTableUpdateCompanionBuilder,
          (
            FinanceRecord,
            BaseReferences<_$AppDatabase, $FinanceRecordsTable, FinanceRecord>,
          ),
          FinanceRecord,
          PrefetchHooks Function()
        > {
  $$FinanceRecordsTableTableManager(
    _$AppDatabase db,
    $FinanceRecordsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$FinanceRecordsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$FinanceRecordsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$FinanceRecordsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> type = const Value.absent(),
                Value<double> amount = const Value.absent(),
                Value<String> category = const Value.absent(),
                Value<String?> description = const Value.absent(),
                Value<DateTime> date = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => FinanceRecordsCompanion(
                id: id,
                type: type,
                amount: amount,
                category: category,
                description: description,
                date: date,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String type,
                required double amount,
                required String category,
                Value<String?> description = const Value.absent(),
                required DateTime date,
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => FinanceRecordsCompanion.insert(
                id: id,
                type: type,
                amount: amount,
                category: category,
                description: description,
                date: date,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$FinanceRecordsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $FinanceRecordsTable,
      FinanceRecord,
      $$FinanceRecordsTableFilterComposer,
      $$FinanceRecordsTableOrderingComposer,
      $$FinanceRecordsTableAnnotationComposer,
      $$FinanceRecordsTableCreateCompanionBuilder,
      $$FinanceRecordsTableUpdateCompanionBuilder,
      (
        FinanceRecord,
        BaseReferences<_$AppDatabase, $FinanceRecordsTable, FinanceRecord>,
      ),
      FinanceRecord,
      PrefetchHooks Function()
    >;
typedef $$HealthRecordsTableCreateCompanionBuilder =
    HealthRecordsCompanion Function({
      required String id,
      required DateTime recordDate,
      Value<double?> weight,
      required int waterIntakeMl,
      required int sleepMinutes,
      required int caloriesConsumed,
      required int exerciseMinutes,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$HealthRecordsTableUpdateCompanionBuilder =
    HealthRecordsCompanion Function({
      Value<String> id,
      Value<DateTime> recordDate,
      Value<double?> weight,
      Value<int> waterIntakeMl,
      Value<int> sleepMinutes,
      Value<int> caloriesConsumed,
      Value<int> exerciseMinutes,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$HealthRecordsTableFilterComposer
    extends Composer<_$AppDatabase, $HealthRecordsTable> {
  $$HealthRecordsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get recordDate => $composableBuilder(
    column: $table.recordDate,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get weight => $composableBuilder(
    column: $table.weight,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get waterIntakeMl => $composableBuilder(
    column: $table.waterIntakeMl,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get sleepMinutes => $composableBuilder(
    column: $table.sleepMinutes,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get caloriesConsumed => $composableBuilder(
    column: $table.caloriesConsumed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get exerciseMinutes => $composableBuilder(
    column: $table.exerciseMinutes,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$HealthRecordsTableOrderingComposer
    extends Composer<_$AppDatabase, $HealthRecordsTable> {
  $$HealthRecordsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get recordDate => $composableBuilder(
    column: $table.recordDate,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get weight => $composableBuilder(
    column: $table.weight,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get waterIntakeMl => $composableBuilder(
    column: $table.waterIntakeMl,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get sleepMinutes => $composableBuilder(
    column: $table.sleepMinutes,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get caloriesConsumed => $composableBuilder(
    column: $table.caloriesConsumed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get exerciseMinutes => $composableBuilder(
    column: $table.exerciseMinutes,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$HealthRecordsTableAnnotationComposer
    extends Composer<_$AppDatabase, $HealthRecordsTable> {
  $$HealthRecordsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<DateTime> get recordDate => $composableBuilder(
    column: $table.recordDate,
    builder: (column) => column,
  );

  GeneratedColumn<double> get weight =>
      $composableBuilder(column: $table.weight, builder: (column) => column);

  GeneratedColumn<int> get waterIntakeMl => $composableBuilder(
    column: $table.waterIntakeMl,
    builder: (column) => column,
  );

  GeneratedColumn<int> get sleepMinutes => $composableBuilder(
    column: $table.sleepMinutes,
    builder: (column) => column,
  );

  GeneratedColumn<int> get caloriesConsumed => $composableBuilder(
    column: $table.caloriesConsumed,
    builder: (column) => column,
  );

  GeneratedColumn<int> get exerciseMinutes => $composableBuilder(
    column: $table.exerciseMinutes,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$HealthRecordsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $HealthRecordsTable,
          HealthRecord,
          $$HealthRecordsTableFilterComposer,
          $$HealthRecordsTableOrderingComposer,
          $$HealthRecordsTableAnnotationComposer,
          $$HealthRecordsTableCreateCompanionBuilder,
          $$HealthRecordsTableUpdateCompanionBuilder,
          (
            HealthRecord,
            BaseReferences<_$AppDatabase, $HealthRecordsTable, HealthRecord>,
          ),
          HealthRecord,
          PrefetchHooks Function()
        > {
  $$HealthRecordsTableTableManager(_$AppDatabase db, $HealthRecordsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$HealthRecordsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$HealthRecordsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$HealthRecordsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<DateTime> recordDate = const Value.absent(),
                Value<double?> weight = const Value.absent(),
                Value<int> waterIntakeMl = const Value.absent(),
                Value<int> sleepMinutes = const Value.absent(),
                Value<int> caloriesConsumed = const Value.absent(),
                Value<int> exerciseMinutes = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => HealthRecordsCompanion(
                id: id,
                recordDate: recordDate,
                weight: weight,
                waterIntakeMl: waterIntakeMl,
                sleepMinutes: sleepMinutes,
                caloriesConsumed: caloriesConsumed,
                exerciseMinutes: exerciseMinutes,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required DateTime recordDate,
                Value<double?> weight = const Value.absent(),
                required int waterIntakeMl,
                required int sleepMinutes,
                required int caloriesConsumed,
                required int exerciseMinutes,
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => HealthRecordsCompanion.insert(
                id: id,
                recordDate: recordDate,
                weight: weight,
                waterIntakeMl: waterIntakeMl,
                sleepMinutes: sleepMinutes,
                caloriesConsumed: caloriesConsumed,
                exerciseMinutes: exerciseMinutes,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$HealthRecordsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $HealthRecordsTable,
      HealthRecord,
      $$HealthRecordsTableFilterComposer,
      $$HealthRecordsTableOrderingComposer,
      $$HealthRecordsTableAnnotationComposer,
      $$HealthRecordsTableCreateCompanionBuilder,
      $$HealthRecordsTableUpdateCompanionBuilder,
      (
        HealthRecord,
        BaseReferences<_$AppDatabase, $HealthRecordsTable, HealthRecord>,
      ),
      HealthRecord,
      PrefetchHooks Function()
    >;
typedef $$MessagesTableCreateCompanionBuilder =
    MessagesCompanion Function({
      required String id,
      required String conversationId,
      required String role,
      required String content,
      Value<String?> modelUsed,
      Value<int?> tokensUsed,
      required DateTime createdAt,
      Value<String?> citationsJson,
      Value<String?> toolCallsJson,
      Value<String?> agentId,
      Value<DateTime?> updatedAt,
      Value<String?> turnId,
      Value<String?> turnMetaJson,
      Value<int> rowid,
    });
typedef $$MessagesTableUpdateCompanionBuilder =
    MessagesCompanion Function({
      Value<String> id,
      Value<String> conversationId,
      Value<String> role,
      Value<String> content,
      Value<String?> modelUsed,
      Value<int?> tokensUsed,
      Value<DateTime> createdAt,
      Value<String?> citationsJson,
      Value<String?> toolCallsJson,
      Value<String?> agentId,
      Value<DateTime?> updatedAt,
      Value<String?> turnId,
      Value<String?> turnMetaJson,
      Value<int> rowid,
    });

class $$MessagesTableFilterComposer
    extends Composer<_$AppDatabase, $MessagesTable> {
  $$MessagesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get conversationId => $composableBuilder(
    column: $table.conversationId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get role => $composableBuilder(
    column: $table.role,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get content =>
      $composableBuilder(
        column: $table.content,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<String> get modelUsed => $composableBuilder(
    column: $table.modelUsed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get tokensUsed => $composableBuilder(
    column: $table.tokensUsed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String?, String, String> get citationsJson =>
      $composableBuilder(
        column: $table.citationsJson,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnWithTypeConverterFilters<String?, String, String> get toolCallsJson =>
      $composableBuilder(
        column: $table.toolCallsJson,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<String> get agentId => $composableBuilder(
    column: $table.agentId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get turnId => $composableBuilder(
    column: $table.turnId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String?, String, String> get turnMetaJson =>
      $composableBuilder(
        column: $table.turnMetaJson,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );
}

class $$MessagesTableOrderingComposer
    extends Composer<_$AppDatabase, $MessagesTable> {
  $$MessagesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get conversationId => $composableBuilder(
    column: $table.conversationId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get role => $composableBuilder(
    column: $table.role,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get content => $composableBuilder(
    column: $table.content,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get modelUsed => $composableBuilder(
    column: $table.modelUsed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get tokensUsed => $composableBuilder(
    column: $table.tokensUsed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get citationsJson => $composableBuilder(
    column: $table.citationsJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get toolCallsJson => $composableBuilder(
    column: $table.toolCallsJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get agentId => $composableBuilder(
    column: $table.agentId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get turnId => $composableBuilder(
    column: $table.turnId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get turnMetaJson => $composableBuilder(
    column: $table.turnMetaJson,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$MessagesTableAnnotationComposer
    extends Composer<_$AppDatabase, $MessagesTable> {
  $$MessagesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get conversationId => $composableBuilder(
    column: $table.conversationId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get role =>
      $composableBuilder(column: $table.role, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get content =>
      $composableBuilder(column: $table.content, builder: (column) => column);

  GeneratedColumn<String> get modelUsed =>
      $composableBuilder(column: $table.modelUsed, builder: (column) => column);

  GeneratedColumn<int> get tokensUsed => $composableBuilder(
    column: $table.tokensUsed,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get citationsJson =>
      $composableBuilder(
        column: $table.citationsJson,
        builder: (column) => column,
      );

  GeneratedColumnWithTypeConverter<String?, String> get toolCallsJson =>
      $composableBuilder(
        column: $table.toolCallsJson,
        builder: (column) => column,
      );

  GeneratedColumn<String> get agentId =>
      $composableBuilder(column: $table.agentId, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);

  GeneratedColumn<String> get turnId =>
      $composableBuilder(column: $table.turnId, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get turnMetaJson =>
      $composableBuilder(
        column: $table.turnMetaJson,
        builder: (column) => column,
      );
}

class $$MessagesTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $MessagesTable,
          Message,
          $$MessagesTableFilterComposer,
          $$MessagesTableOrderingComposer,
          $$MessagesTableAnnotationComposer,
          $$MessagesTableCreateCompanionBuilder,
          $$MessagesTableUpdateCompanionBuilder,
          (Message, BaseReferences<_$AppDatabase, $MessagesTable, Message>),
          Message,
          PrefetchHooks Function()
        > {
  $$MessagesTableTableManager(_$AppDatabase db, $MessagesTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$MessagesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$MessagesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$MessagesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> conversationId = const Value.absent(),
                Value<String> role = const Value.absent(),
                Value<String> content = const Value.absent(),
                Value<String?> modelUsed = const Value.absent(),
                Value<int?> tokensUsed = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<String?> citationsJson = const Value.absent(),
                Value<String?> toolCallsJson = const Value.absent(),
                Value<String?> agentId = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<String?> turnId = const Value.absent(),
                Value<String?> turnMetaJson = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => MessagesCompanion(
                id: id,
                conversationId: conversationId,
                role: role,
                content: content,
                modelUsed: modelUsed,
                tokensUsed: tokensUsed,
                createdAt: createdAt,
                citationsJson: citationsJson,
                toolCallsJson: toolCallsJson,
                agentId: agentId,
                updatedAt: updatedAt,
                turnId: turnId,
                turnMetaJson: turnMetaJson,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String conversationId,
                required String role,
                required String content,
                Value<String?> modelUsed = const Value.absent(),
                Value<int?> tokensUsed = const Value.absent(),
                required DateTime createdAt,
                Value<String?> citationsJson = const Value.absent(),
                Value<String?> toolCallsJson = const Value.absent(),
                Value<String?> agentId = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<String?> turnId = const Value.absent(),
                Value<String?> turnMetaJson = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => MessagesCompanion.insert(
                id: id,
                conversationId: conversationId,
                role: role,
                content: content,
                modelUsed: modelUsed,
                tokensUsed: tokensUsed,
                createdAt: createdAt,
                citationsJson: citationsJson,
                toolCallsJson: toolCallsJson,
                agentId: agentId,
                updatedAt: updatedAt,
                turnId: turnId,
                turnMetaJson: turnMetaJson,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$MessagesTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $MessagesTable,
      Message,
      $$MessagesTableFilterComposer,
      $$MessagesTableOrderingComposer,
      $$MessagesTableAnnotationComposer,
      $$MessagesTableCreateCompanionBuilder,
      $$MessagesTableUpdateCompanionBuilder,
      (Message, BaseReferences<_$AppDatabase, $MessagesTable, Message>),
      Message,
      PrefetchHooks Function()
    >;
typedef $$AgentConfigurationsTableCreateCompanionBuilder =
    AgentConfigurationsCompanion Function({
      required String agentId,
      required String systemInstruction,
      required DateTime updatedAt,
      Value<int> rowid,
    });
typedef $$AgentConfigurationsTableUpdateCompanionBuilder =
    AgentConfigurationsCompanion Function({
      Value<String> agentId,
      Value<String> systemInstruction,
      Value<DateTime> updatedAt,
      Value<int> rowid,
    });

class $$AgentConfigurationsTableFilterComposer
    extends Composer<_$AppDatabase, $AgentConfigurationsTable> {
  $$AgentConfigurationsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get agentId => $composableBuilder(
    column: $table.agentId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get systemInstruction => $composableBuilder(
    column: $table.systemInstruction,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$AgentConfigurationsTableOrderingComposer
    extends Composer<_$AppDatabase, $AgentConfigurationsTable> {
  $$AgentConfigurationsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get agentId => $composableBuilder(
    column: $table.agentId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get systemInstruction => $composableBuilder(
    column: $table.systemInstruction,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$AgentConfigurationsTableAnnotationComposer
    extends Composer<_$AppDatabase, $AgentConfigurationsTable> {
  $$AgentConfigurationsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get agentId =>
      $composableBuilder(column: $table.agentId, builder: (column) => column);

  GeneratedColumn<String> get systemInstruction => $composableBuilder(
    column: $table.systemInstruction,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$AgentConfigurationsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $AgentConfigurationsTable,
          AgentConfiguration,
          $$AgentConfigurationsTableFilterComposer,
          $$AgentConfigurationsTableOrderingComposer,
          $$AgentConfigurationsTableAnnotationComposer,
          $$AgentConfigurationsTableCreateCompanionBuilder,
          $$AgentConfigurationsTableUpdateCompanionBuilder,
          (
            AgentConfiguration,
            BaseReferences<
              _$AppDatabase,
              $AgentConfigurationsTable,
              AgentConfiguration
            >,
          ),
          AgentConfiguration,
          PrefetchHooks Function()
        > {
  $$AgentConfigurationsTableTableManager(
    _$AppDatabase db,
    $AgentConfigurationsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$AgentConfigurationsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$AgentConfigurationsTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$AgentConfigurationsTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> agentId = const Value.absent(),
                Value<String> systemInstruction = const Value.absent(),
                Value<DateTime> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => AgentConfigurationsCompanion(
                agentId: agentId,
                systemInstruction: systemInstruction,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String agentId,
                required String systemInstruction,
                required DateTime updatedAt,
                Value<int> rowid = const Value.absent(),
              }) => AgentConfigurationsCompanion.insert(
                agentId: agentId,
                systemInstruction: systemInstruction,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$AgentConfigurationsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $AgentConfigurationsTable,
      AgentConfiguration,
      $$AgentConfigurationsTableFilterComposer,
      $$AgentConfigurationsTableOrderingComposer,
      $$AgentConfigurationsTableAnnotationComposer,
      $$AgentConfigurationsTableCreateCompanionBuilder,
      $$AgentConfigurationsTableUpdateCompanionBuilder,
      (
        AgentConfiguration,
        BaseReferences<
          _$AppDatabase,
          $AgentConfigurationsTable,
          AgentConfiguration
        >,
      ),
      AgentConfiguration,
      PrefetchHooks Function()
    >;
typedef $$UserMemoriesTableCreateCompanionBuilder =
    UserMemoriesCompanion Function({
      required String id,
      required String fact,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$UserMemoriesTableUpdateCompanionBuilder =
    UserMemoriesCompanion Function({
      Value<String> id,
      Value<String> fact,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$UserMemoriesTableFilterComposer
    extends Composer<_$AppDatabase, $UserMemoriesTable> {
  $$UserMemoriesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get fact =>
      $composableBuilder(
        column: $table.fact,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$UserMemoriesTableOrderingComposer
    extends Composer<_$AppDatabase, $UserMemoriesTable> {
  $$UserMemoriesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get fact => $composableBuilder(
    column: $table.fact,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$UserMemoriesTableAnnotationComposer
    extends Composer<_$AppDatabase, $UserMemoriesTable> {
  $$UserMemoriesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get fact =>
      $composableBuilder(column: $table.fact, builder: (column) => column);

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$UserMemoriesTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $UserMemoriesTable,
          UserMemory,
          $$UserMemoriesTableFilterComposer,
          $$UserMemoriesTableOrderingComposer,
          $$UserMemoriesTableAnnotationComposer,
          $$UserMemoriesTableCreateCompanionBuilder,
          $$UserMemoriesTableUpdateCompanionBuilder,
          (
            UserMemory,
            BaseReferences<_$AppDatabase, $UserMemoriesTable, UserMemory>,
          ),
          UserMemory,
          PrefetchHooks Function()
        > {
  $$UserMemoriesTableTableManager(_$AppDatabase db, $UserMemoriesTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$UserMemoriesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$UserMemoriesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$UserMemoriesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> fact = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => UserMemoriesCompanion(
                id: id,
                fact: fact,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String fact,
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => UserMemoriesCompanion.insert(
                id: id,
                fact: fact,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$UserMemoriesTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $UserMemoriesTable,
      UserMemory,
      $$UserMemoriesTableFilterComposer,
      $$UserMemoriesTableOrderingComposer,
      $$UserMemoriesTableAnnotationComposer,
      $$UserMemoriesTableCreateCompanionBuilder,
      $$UserMemoriesTableUpdateCompanionBuilder,
      (
        UserMemory,
        BaseReferences<_$AppDatabase, $UserMemoriesTable, UserMemory>,
      ),
      UserMemory,
      PrefetchHooks Function()
    >;
typedef $$ProjectsTableCreateCompanionBuilder =
    ProjectsCompanion Function({
      required String id,
      required String title,
      Value<String?> description,
      required String status,
      Value<DateTime?> targetDate,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$ProjectsTableUpdateCompanionBuilder =
    ProjectsCompanion Function({
      Value<String> id,
      Value<String> title,
      Value<String?> description,
      Value<String> status,
      Value<DateTime?> targetDate,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$ProjectsTableFilterComposer
    extends Composer<_$AppDatabase, $ProjectsTable> {
  $$ProjectsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get title =>
      $composableBuilder(
        column: $table.title,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnWithTypeConverterFilters<String?, String, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get targetDate => $composableBuilder(
    column: $table.targetDate,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$ProjectsTableOrderingComposer
    extends Composer<_$AppDatabase, $ProjectsTable> {
  $$ProjectsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get title => $composableBuilder(
    column: $table.title,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get description => $composableBuilder(
    column: $table.description,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get targetDate => $composableBuilder(
    column: $table.targetDate,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$ProjectsTableAnnotationComposer
    extends Composer<_$AppDatabase, $ProjectsTable> {
  $$ProjectsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get title =>
      $composableBuilder(column: $table.title, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => column,
      );

  GeneratedColumn<String> get status =>
      $composableBuilder(column: $table.status, builder: (column) => column);

  GeneratedColumn<DateTime> get targetDate => $composableBuilder(
    column: $table.targetDate,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$ProjectsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $ProjectsTable,
          Project,
          $$ProjectsTableFilterComposer,
          $$ProjectsTableOrderingComposer,
          $$ProjectsTableAnnotationComposer,
          $$ProjectsTableCreateCompanionBuilder,
          $$ProjectsTableUpdateCompanionBuilder,
          (Project, BaseReferences<_$AppDatabase, $ProjectsTable, Project>),
          Project,
          PrefetchHooks Function()
        > {
  $$ProjectsTableTableManager(_$AppDatabase db, $ProjectsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$ProjectsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$ProjectsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$ProjectsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> title = const Value.absent(),
                Value<String?> description = const Value.absent(),
                Value<String> status = const Value.absent(),
                Value<DateTime?> targetDate = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => ProjectsCompanion(
                id: id,
                title: title,
                description: description,
                status: status,
                targetDate: targetDate,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String title,
                Value<String?> description = const Value.absent(),
                required String status,
                Value<DateTime?> targetDate = const Value.absent(),
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => ProjectsCompanion.insert(
                id: id,
                title: title,
                description: description,
                status: status,
                targetDate: targetDate,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$ProjectsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $ProjectsTable,
      Project,
      $$ProjectsTableFilterComposer,
      $$ProjectsTableOrderingComposer,
      $$ProjectsTableAnnotationComposer,
      $$ProjectsTableCreateCompanionBuilder,
      $$ProjectsTableUpdateCompanionBuilder,
      (Project, BaseReferences<_$AppDatabase, $ProjectsTable, Project>),
      Project,
      PrefetchHooks Function()
    >;
typedef $$GoalsTableCreateCompanionBuilder =
    GoalsCompanion Function({
      required String id,
      required String title,
      Value<String?> description,
      Value<String?> projectId,
      Value<String?> category,
      required String status,
      required String priority,
      Value<DateTime?> targetDate,
      Value<int> progressPct,
      required DateTime createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });
typedef $$GoalsTableUpdateCompanionBuilder =
    GoalsCompanion Function({
      Value<String> id,
      Value<String> title,
      Value<String?> description,
      Value<String?> projectId,
      Value<String?> category,
      Value<String> status,
      Value<String> priority,
      Value<DateTime?> targetDate,
      Value<int> progressPct,
      Value<DateTime> createdAt,
      Value<bool> isSynced,
      Value<DateTime?> updatedAt,
      Value<int> rowid,
    });

class $$GoalsTableFilterComposer extends Composer<_$AppDatabase, $GoalsTable> {
  $$GoalsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get title =>
      $composableBuilder(
        column: $table.title,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnWithTypeConverterFilters<String?, String, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<String> get projectId => $composableBuilder(
    column: $table.projectId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get category => $composableBuilder(
    column: $table.category,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get priority => $composableBuilder(
    column: $table.priority,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get targetDate => $composableBuilder(
    column: $table.targetDate,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get progressPct => $composableBuilder(
    column: $table.progressPct,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$GoalsTableOrderingComposer
    extends Composer<_$AppDatabase, $GoalsTable> {
  $$GoalsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get title => $composableBuilder(
    column: $table.title,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get description => $composableBuilder(
    column: $table.description,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get projectId => $composableBuilder(
    column: $table.projectId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get category => $composableBuilder(
    column: $table.category,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get status => $composableBuilder(
    column: $table.status,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get priority => $composableBuilder(
    column: $table.priority,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get targetDate => $composableBuilder(
    column: $table.targetDate,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get progressPct => $composableBuilder(
    column: $table.progressPct,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isSynced => $composableBuilder(
    column: $table.isSynced,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$GoalsTableAnnotationComposer
    extends Composer<_$AppDatabase, $GoalsTable> {
  $$GoalsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get title =>
      $composableBuilder(column: $table.title, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String?, String> get description =>
      $composableBuilder(
        column: $table.description,
        builder: (column) => column,
      );

  GeneratedColumn<String> get projectId =>
      $composableBuilder(column: $table.projectId, builder: (column) => column);

  GeneratedColumn<String> get category =>
      $composableBuilder(column: $table.category, builder: (column) => column);

  GeneratedColumn<String> get status =>
      $composableBuilder(column: $table.status, builder: (column) => column);

  GeneratedColumn<String> get priority =>
      $composableBuilder(column: $table.priority, builder: (column) => column);

  GeneratedColumn<DateTime> get targetDate => $composableBuilder(
    column: $table.targetDate,
    builder: (column) => column,
  );

  GeneratedColumn<int> get progressPct => $composableBuilder(
    column: $table.progressPct,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<bool> get isSynced =>
      $composableBuilder(column: $table.isSynced, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$GoalsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $GoalsTable,
          Goal,
          $$GoalsTableFilterComposer,
          $$GoalsTableOrderingComposer,
          $$GoalsTableAnnotationComposer,
          $$GoalsTableCreateCompanionBuilder,
          $$GoalsTableUpdateCompanionBuilder,
          (Goal, BaseReferences<_$AppDatabase, $GoalsTable, Goal>),
          Goal,
          PrefetchHooks Function()
        > {
  $$GoalsTableTableManager(_$AppDatabase db, $GoalsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$GoalsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$GoalsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$GoalsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> title = const Value.absent(),
                Value<String?> description = const Value.absent(),
                Value<String?> projectId = const Value.absent(),
                Value<String?> category = const Value.absent(),
                Value<String> status = const Value.absent(),
                Value<String> priority = const Value.absent(),
                Value<DateTime?> targetDate = const Value.absent(),
                Value<int> progressPct = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => GoalsCompanion(
                id: id,
                title: title,
                description: description,
                projectId: projectId,
                category: category,
                status: status,
                priority: priority,
                targetDate: targetDate,
                progressPct: progressPct,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String title,
                Value<String?> description = const Value.absent(),
                Value<String?> projectId = const Value.absent(),
                Value<String?> category = const Value.absent(),
                required String status,
                required String priority,
                Value<DateTime?> targetDate = const Value.absent(),
                Value<int> progressPct = const Value.absent(),
                required DateTime createdAt,
                Value<bool> isSynced = const Value.absent(),
                Value<DateTime?> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => GoalsCompanion.insert(
                id: id,
                title: title,
                description: description,
                projectId: projectId,
                category: category,
                status: status,
                priority: priority,
                targetDate: targetDate,
                progressPct: progressPct,
                createdAt: createdAt,
                isSynced: isSynced,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$GoalsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $GoalsTable,
      Goal,
      $$GoalsTableFilterComposer,
      $$GoalsTableOrderingComposer,
      $$GoalsTableAnnotationComposer,
      $$GoalsTableCreateCompanionBuilder,
      $$GoalsTableUpdateCompanionBuilder,
      (Goal, BaseReferences<_$AppDatabase, $GoalsTable, Goal>),
      Goal,
      PrefetchHooks Function()
    >;
typedef $$PendingDeletesTableCreateCompanionBuilder =
    PendingDeletesCompanion Function({
      required String recordId,
      required String targetTable,
      required DateTime deletedAt,
      Value<int> rowid,
    });
typedef $$PendingDeletesTableUpdateCompanionBuilder =
    PendingDeletesCompanion Function({
      Value<String> recordId,
      Value<String> targetTable,
      Value<DateTime> deletedAt,
      Value<int> rowid,
    });

class $$PendingDeletesTableFilterComposer
    extends Composer<_$AppDatabase, $PendingDeletesTable> {
  $$PendingDeletesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get recordId => $composableBuilder(
    column: $table.recordId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get targetTable => $composableBuilder(
    column: $table.targetTable,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get deletedAt => $composableBuilder(
    column: $table.deletedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$PendingDeletesTableOrderingComposer
    extends Composer<_$AppDatabase, $PendingDeletesTable> {
  $$PendingDeletesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get recordId => $composableBuilder(
    column: $table.recordId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get targetTable => $composableBuilder(
    column: $table.targetTable,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get deletedAt => $composableBuilder(
    column: $table.deletedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$PendingDeletesTableAnnotationComposer
    extends Composer<_$AppDatabase, $PendingDeletesTable> {
  $$PendingDeletesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get recordId =>
      $composableBuilder(column: $table.recordId, builder: (column) => column);

  GeneratedColumn<String> get targetTable => $composableBuilder(
    column: $table.targetTable,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get deletedAt =>
      $composableBuilder(column: $table.deletedAt, builder: (column) => column);
}

class $$PendingDeletesTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $PendingDeletesTable,
          PendingDelete,
          $$PendingDeletesTableFilterComposer,
          $$PendingDeletesTableOrderingComposer,
          $$PendingDeletesTableAnnotationComposer,
          $$PendingDeletesTableCreateCompanionBuilder,
          $$PendingDeletesTableUpdateCompanionBuilder,
          (
            PendingDelete,
            BaseReferences<_$AppDatabase, $PendingDeletesTable, PendingDelete>,
          ),
          PendingDelete,
          PrefetchHooks Function()
        > {
  $$PendingDeletesTableTableManager(
    _$AppDatabase db,
    $PendingDeletesTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$PendingDeletesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$PendingDeletesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$PendingDeletesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> recordId = const Value.absent(),
                Value<String> targetTable = const Value.absent(),
                Value<DateTime> deletedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => PendingDeletesCompanion(
                recordId: recordId,
                targetTable: targetTable,
                deletedAt: deletedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String recordId,
                required String targetTable,
                required DateTime deletedAt,
                Value<int> rowid = const Value.absent(),
              }) => PendingDeletesCompanion.insert(
                recordId: recordId,
                targetTable: targetTable,
                deletedAt: deletedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$PendingDeletesTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $PendingDeletesTable,
      PendingDelete,
      $$PendingDeletesTableFilterComposer,
      $$PendingDeletesTableOrderingComposer,
      $$PendingDeletesTableAnnotationComposer,
      $$PendingDeletesTableCreateCompanionBuilder,
      $$PendingDeletesTableUpdateCompanionBuilder,
      (
        PendingDelete,
        BaseReferences<_$AppDatabase, $PendingDeletesTable, PendingDelete>,
      ),
      PendingDelete,
      PrefetchHooks Function()
    >;
typedef $$LearningLogsTableCreateCompanionBuilder =
    LearningLogsCompanion Function({
      required String id,
      required String prompt,
      required String response,
      required int rating,
      Value<String?> correction,
      Value<String?> modelUsed,
      required DateTime createdAt,
      Value<int> rowid,
    });
typedef $$LearningLogsTableUpdateCompanionBuilder =
    LearningLogsCompanion Function({
      Value<String> id,
      Value<String> prompt,
      Value<String> response,
      Value<int> rating,
      Value<String?> correction,
      Value<String?> modelUsed,
      Value<DateTime> createdAt,
      Value<int> rowid,
    });

class $$LearningLogsTableFilterComposer
    extends Composer<_$AppDatabase, $LearningLogsTable> {
  $$LearningLogsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnWithTypeConverterFilters<String, String, String> get prompt =>
      $composableBuilder(
        column: $table.prompt,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnWithTypeConverterFilters<String, String, String> get response =>
      $composableBuilder(
        column: $table.response,
        builder: (column) => ColumnWithTypeConverterFilters(column),
      );

  ColumnFilters<int> get rating => $composableBuilder(
    column: $table.rating,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get correction => $composableBuilder(
    column: $table.correction,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get modelUsed => $composableBuilder(
    column: $table.modelUsed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$LearningLogsTableOrderingComposer
    extends Composer<_$AppDatabase, $LearningLogsTable> {
  $$LearningLogsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get prompt => $composableBuilder(
    column: $table.prompt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get response => $composableBuilder(
    column: $table.response,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get rating => $composableBuilder(
    column: $table.rating,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get correction => $composableBuilder(
    column: $table.correction,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get modelUsed => $composableBuilder(
    column: $table.modelUsed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$LearningLogsTableAnnotationComposer
    extends Composer<_$AppDatabase, $LearningLogsTable> {
  $$LearningLogsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get prompt =>
      $composableBuilder(column: $table.prompt, builder: (column) => column);

  GeneratedColumnWithTypeConverter<String, String> get response =>
      $composableBuilder(column: $table.response, builder: (column) => column);

  GeneratedColumn<int> get rating =>
      $composableBuilder(column: $table.rating, builder: (column) => column);

  GeneratedColumn<String> get correction => $composableBuilder(
    column: $table.correction,
    builder: (column) => column,
  );

  GeneratedColumn<String> get modelUsed =>
      $composableBuilder(column: $table.modelUsed, builder: (column) => column);

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);
}

class $$LearningLogsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $LearningLogsTable,
          LearningLog,
          $$LearningLogsTableFilterComposer,
          $$LearningLogsTableOrderingComposer,
          $$LearningLogsTableAnnotationComposer,
          $$LearningLogsTableCreateCompanionBuilder,
          $$LearningLogsTableUpdateCompanionBuilder,
          (
            LearningLog,
            BaseReferences<_$AppDatabase, $LearningLogsTable, LearningLog>,
          ),
          LearningLog,
          PrefetchHooks Function()
        > {
  $$LearningLogsTableTableManager(_$AppDatabase db, $LearningLogsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$LearningLogsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$LearningLogsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$LearningLogsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> prompt = const Value.absent(),
                Value<String> response = const Value.absent(),
                Value<int> rating = const Value.absent(),
                Value<String?> correction = const Value.absent(),
                Value<String?> modelUsed = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => LearningLogsCompanion(
                id: id,
                prompt: prompt,
                response: response,
                rating: rating,
                correction: correction,
                modelUsed: modelUsed,
                createdAt: createdAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String prompt,
                required String response,
                required int rating,
                Value<String?> correction = const Value.absent(),
                Value<String?> modelUsed = const Value.absent(),
                required DateTime createdAt,
                Value<int> rowid = const Value.absent(),
              }) => LearningLogsCompanion.insert(
                id: id,
                prompt: prompt,
                response: response,
                rating: rating,
                correction: correction,
                modelUsed: modelUsed,
                createdAt: createdAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$LearningLogsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $LearningLogsTable,
      LearningLog,
      $$LearningLogsTableFilterComposer,
      $$LearningLogsTableOrderingComposer,
      $$LearningLogsTableAnnotationComposer,
      $$LearningLogsTableCreateCompanionBuilder,
      $$LearningLogsTableUpdateCompanionBuilder,
      (
        LearningLog,
        BaseReferences<_$AppDatabase, $LearningLogsTable, LearningLog>,
      ),
      LearningLog,
      PrefetchHooks Function()
    >;
typedef $$OfflineModelWeightsTableCreateCompanionBuilder =
    OfflineModelWeightsCompanion Function({
      required String id,
      required String modelName,
      required String sizeBytes,
      Value<String?> localPath,
      Value<bool> isDownloaded,
      Value<bool> isActive,
      required DateTime updatedAt,
      Value<int> rowid,
    });
typedef $$OfflineModelWeightsTableUpdateCompanionBuilder =
    OfflineModelWeightsCompanion Function({
      Value<String> id,
      Value<String> modelName,
      Value<String> sizeBytes,
      Value<String?> localPath,
      Value<bool> isDownloaded,
      Value<bool> isActive,
      Value<DateTime> updatedAt,
      Value<int> rowid,
    });

class $$OfflineModelWeightsTableFilterComposer
    extends Composer<_$AppDatabase, $OfflineModelWeightsTable> {
  $$OfflineModelWeightsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get modelName => $composableBuilder(
    column: $table.modelName,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get sizeBytes => $composableBuilder(
    column: $table.sizeBytes,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get localPath => $composableBuilder(
    column: $table.localPath,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isDownloaded => $composableBuilder(
    column: $table.isDownloaded,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isActive => $composableBuilder(
    column: $table.isActive,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$OfflineModelWeightsTableOrderingComposer
    extends Composer<_$AppDatabase, $OfflineModelWeightsTable> {
  $$OfflineModelWeightsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get modelName => $composableBuilder(
    column: $table.modelName,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get sizeBytes => $composableBuilder(
    column: $table.sizeBytes,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get localPath => $composableBuilder(
    column: $table.localPath,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isDownloaded => $composableBuilder(
    column: $table.isDownloaded,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isActive => $composableBuilder(
    column: $table.isActive,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$OfflineModelWeightsTableAnnotationComposer
    extends Composer<_$AppDatabase, $OfflineModelWeightsTable> {
  $$OfflineModelWeightsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get modelName =>
      $composableBuilder(column: $table.modelName, builder: (column) => column);

  GeneratedColumn<String> get sizeBytes =>
      $composableBuilder(column: $table.sizeBytes, builder: (column) => column);

  GeneratedColumn<String> get localPath =>
      $composableBuilder(column: $table.localPath, builder: (column) => column);

  GeneratedColumn<bool> get isDownloaded => $composableBuilder(
    column: $table.isDownloaded,
    builder: (column) => column,
  );

  GeneratedColumn<bool> get isActive =>
      $composableBuilder(column: $table.isActive, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$OfflineModelWeightsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $OfflineModelWeightsTable,
          OfflineModelWeight,
          $$OfflineModelWeightsTableFilterComposer,
          $$OfflineModelWeightsTableOrderingComposer,
          $$OfflineModelWeightsTableAnnotationComposer,
          $$OfflineModelWeightsTableCreateCompanionBuilder,
          $$OfflineModelWeightsTableUpdateCompanionBuilder,
          (
            OfflineModelWeight,
            BaseReferences<
              _$AppDatabase,
              $OfflineModelWeightsTable,
              OfflineModelWeight
            >,
          ),
          OfflineModelWeight,
          PrefetchHooks Function()
        > {
  $$OfflineModelWeightsTableTableManager(
    _$AppDatabase db,
    $OfflineModelWeightsTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$OfflineModelWeightsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$OfflineModelWeightsTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$OfflineModelWeightsTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> modelName = const Value.absent(),
                Value<String> sizeBytes = const Value.absent(),
                Value<String?> localPath = const Value.absent(),
                Value<bool> isDownloaded = const Value.absent(),
                Value<bool> isActive = const Value.absent(),
                Value<DateTime> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => OfflineModelWeightsCompanion(
                id: id,
                modelName: modelName,
                sizeBytes: sizeBytes,
                localPath: localPath,
                isDownloaded: isDownloaded,
                isActive: isActive,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String modelName,
                required String sizeBytes,
                Value<String?> localPath = const Value.absent(),
                Value<bool> isDownloaded = const Value.absent(),
                Value<bool> isActive = const Value.absent(),
                required DateTime updatedAt,
                Value<int> rowid = const Value.absent(),
              }) => OfflineModelWeightsCompanion.insert(
                id: id,
                modelName: modelName,
                sizeBytes: sizeBytes,
                localPath: localPath,
                isDownloaded: isDownloaded,
                isActive: isActive,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$OfflineModelWeightsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $OfflineModelWeightsTable,
      OfflineModelWeight,
      $$OfflineModelWeightsTableFilterComposer,
      $$OfflineModelWeightsTableOrderingComposer,
      $$OfflineModelWeightsTableAnnotationComposer,
      $$OfflineModelWeightsTableCreateCompanionBuilder,
      $$OfflineModelWeightsTableUpdateCompanionBuilder,
      (
        OfflineModelWeight,
        BaseReferences<
          _$AppDatabase,
          $OfflineModelWeightsTable,
          OfflineModelWeight
        >,
      ),
      OfflineModelWeight,
      PrefetchHooks Function()
    >;
typedef $$TelemetryLogsTableCreateCompanionBuilder =
    TelemetryLogsCompanion Function({
      required String id,
      required String eventType,
      required double latencyMs,
      required String agentUsed,
      required String modelUsed,
      required bool isOffline,
      Value<String?> errorMessage,
      Value<int?> feedbackRating,
      required DateTime createdAt,
      Value<int> rowid,
    });
typedef $$TelemetryLogsTableUpdateCompanionBuilder =
    TelemetryLogsCompanion Function({
      Value<String> id,
      Value<String> eventType,
      Value<double> latencyMs,
      Value<String> agentUsed,
      Value<String> modelUsed,
      Value<bool> isOffline,
      Value<String?> errorMessage,
      Value<int?> feedbackRating,
      Value<DateTime> createdAt,
      Value<int> rowid,
    });

class $$TelemetryLogsTableFilterComposer
    extends Composer<_$AppDatabase, $TelemetryLogsTable> {
  $$TelemetryLogsTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get eventType => $composableBuilder(
    column: $table.eventType,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get latencyMs => $composableBuilder(
    column: $table.latencyMs,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get agentUsed => $composableBuilder(
    column: $table.agentUsed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get modelUsed => $composableBuilder(
    column: $table.modelUsed,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<bool> get isOffline => $composableBuilder(
    column: $table.isOffline,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get errorMessage => $composableBuilder(
    column: $table.errorMessage,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get feedbackRating => $composableBuilder(
    column: $table.feedbackRating,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$TelemetryLogsTableOrderingComposer
    extends Composer<_$AppDatabase, $TelemetryLogsTable> {
  $$TelemetryLogsTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get eventType => $composableBuilder(
    column: $table.eventType,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get latencyMs => $composableBuilder(
    column: $table.latencyMs,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get agentUsed => $composableBuilder(
    column: $table.agentUsed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get modelUsed => $composableBuilder(
    column: $table.modelUsed,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<bool> get isOffline => $composableBuilder(
    column: $table.isOffline,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get errorMessage => $composableBuilder(
    column: $table.errorMessage,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get feedbackRating => $composableBuilder(
    column: $table.feedbackRating,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$TelemetryLogsTableAnnotationComposer
    extends Composer<_$AppDatabase, $TelemetryLogsTable> {
  $$TelemetryLogsTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get eventType =>
      $composableBuilder(column: $table.eventType, builder: (column) => column);

  GeneratedColumn<double> get latencyMs =>
      $composableBuilder(column: $table.latencyMs, builder: (column) => column);

  GeneratedColumn<String> get agentUsed =>
      $composableBuilder(column: $table.agentUsed, builder: (column) => column);

  GeneratedColumn<String> get modelUsed =>
      $composableBuilder(column: $table.modelUsed, builder: (column) => column);

  GeneratedColumn<bool> get isOffline =>
      $composableBuilder(column: $table.isOffline, builder: (column) => column);

  GeneratedColumn<String> get errorMessage => $composableBuilder(
    column: $table.errorMessage,
    builder: (column) => column,
  );

  GeneratedColumn<int> get feedbackRating => $composableBuilder(
    column: $table.feedbackRating,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);
}

class $$TelemetryLogsTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $TelemetryLogsTable,
          TelemetryLog,
          $$TelemetryLogsTableFilterComposer,
          $$TelemetryLogsTableOrderingComposer,
          $$TelemetryLogsTableAnnotationComposer,
          $$TelemetryLogsTableCreateCompanionBuilder,
          $$TelemetryLogsTableUpdateCompanionBuilder,
          (
            TelemetryLog,
            BaseReferences<_$AppDatabase, $TelemetryLogsTable, TelemetryLog>,
          ),
          TelemetryLog,
          PrefetchHooks Function()
        > {
  $$TelemetryLogsTableTableManager(_$AppDatabase db, $TelemetryLogsTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$TelemetryLogsTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$TelemetryLogsTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$TelemetryLogsTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> eventType = const Value.absent(),
                Value<double> latencyMs = const Value.absent(),
                Value<String> agentUsed = const Value.absent(),
                Value<String> modelUsed = const Value.absent(),
                Value<bool> isOffline = const Value.absent(),
                Value<String?> errorMessage = const Value.absent(),
                Value<int?> feedbackRating = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => TelemetryLogsCompanion(
                id: id,
                eventType: eventType,
                latencyMs: latencyMs,
                agentUsed: agentUsed,
                modelUsed: modelUsed,
                isOffline: isOffline,
                errorMessage: errorMessage,
                feedbackRating: feedbackRating,
                createdAt: createdAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String eventType,
                required double latencyMs,
                required String agentUsed,
                required String modelUsed,
                required bool isOffline,
                Value<String?> errorMessage = const Value.absent(),
                Value<int?> feedbackRating = const Value.absent(),
                required DateTime createdAt,
                Value<int> rowid = const Value.absent(),
              }) => TelemetryLogsCompanion.insert(
                id: id,
                eventType: eventType,
                latencyMs: latencyMs,
                agentUsed: agentUsed,
                modelUsed: modelUsed,
                isOffline: isOffline,
                errorMessage: errorMessage,
                feedbackRating: feedbackRating,
                createdAt: createdAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$TelemetryLogsTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $TelemetryLogsTable,
      TelemetryLog,
      $$TelemetryLogsTableFilterComposer,
      $$TelemetryLogsTableOrderingComposer,
      $$TelemetryLogsTableAnnotationComposer,
      $$TelemetryLogsTableCreateCompanionBuilder,
      $$TelemetryLogsTableUpdateCompanionBuilder,
      (
        TelemetryLog,
        BaseReferences<_$AppDatabase, $TelemetryLogsTable, TelemetryLog>,
      ),
      TelemetryLog,
      PrefetchHooks Function()
    >;
typedef $$OutboxEntriesTableCreateCompanionBuilder =
    OutboxEntriesCompanion Function({
      required String id,
      required String targetTable,
      required String recordId,
      required String operation,
      Value<String?> payloadJson,
      required DateTime clientUpdatedAt,
      required DateTime createdAt,
      Value<int> attempts,
      Value<String?> lastError,
      Value<int> rowid,
    });
typedef $$OutboxEntriesTableUpdateCompanionBuilder =
    OutboxEntriesCompanion Function({
      Value<String> id,
      Value<String> targetTable,
      Value<String> recordId,
      Value<String> operation,
      Value<String?> payloadJson,
      Value<DateTime> clientUpdatedAt,
      Value<DateTime> createdAt,
      Value<int> attempts,
      Value<String?> lastError,
      Value<int> rowid,
    });

class $$OutboxEntriesTableFilterComposer
    extends Composer<_$AppDatabase, $OutboxEntriesTable> {
  $$OutboxEntriesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get targetTable => $composableBuilder(
    column: $table.targetTable,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get recordId => $composableBuilder(
    column: $table.recordId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get operation => $composableBuilder(
    column: $table.operation,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get clientUpdatedAt => $composableBuilder(
    column: $table.clientUpdatedAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<int> get attempts => $composableBuilder(
    column: $table.attempts,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get lastError => $composableBuilder(
    column: $table.lastError,
    builder: (column) => ColumnFilters(column),
  );
}

class $$OutboxEntriesTableOrderingComposer
    extends Composer<_$AppDatabase, $OutboxEntriesTable> {
  $$OutboxEntriesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get targetTable => $composableBuilder(
    column: $table.targetTable,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get recordId => $composableBuilder(
    column: $table.recordId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get operation => $composableBuilder(
    column: $table.operation,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get clientUpdatedAt => $composableBuilder(
    column: $table.clientUpdatedAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get createdAt => $composableBuilder(
    column: $table.createdAt,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<int> get attempts => $composableBuilder(
    column: $table.attempts,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get lastError => $composableBuilder(
    column: $table.lastError,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$OutboxEntriesTableAnnotationComposer
    extends Composer<_$AppDatabase, $OutboxEntriesTable> {
  $$OutboxEntriesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get targetTable => $composableBuilder(
    column: $table.targetTable,
    builder: (column) => column,
  );

  GeneratedColumn<String> get recordId =>
      $composableBuilder(column: $table.recordId, builder: (column) => column);

  GeneratedColumn<String> get operation =>
      $composableBuilder(column: $table.operation, builder: (column) => column);

  GeneratedColumn<String> get payloadJson => $composableBuilder(
    column: $table.payloadJson,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get clientUpdatedAt => $composableBuilder(
    column: $table.clientUpdatedAt,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get createdAt =>
      $composableBuilder(column: $table.createdAt, builder: (column) => column);

  GeneratedColumn<int> get attempts =>
      $composableBuilder(column: $table.attempts, builder: (column) => column);

  GeneratedColumn<String> get lastError =>
      $composableBuilder(column: $table.lastError, builder: (column) => column);
}

class $$OutboxEntriesTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $OutboxEntriesTable,
          OutboxEntry,
          $$OutboxEntriesTableFilterComposer,
          $$OutboxEntriesTableOrderingComposer,
          $$OutboxEntriesTableAnnotationComposer,
          $$OutboxEntriesTableCreateCompanionBuilder,
          $$OutboxEntriesTableUpdateCompanionBuilder,
          (
            OutboxEntry,
            BaseReferences<_$AppDatabase, $OutboxEntriesTable, OutboxEntry>,
          ),
          OutboxEntry,
          PrefetchHooks Function()
        > {
  $$OutboxEntriesTableTableManager(_$AppDatabase db, $OutboxEntriesTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$OutboxEntriesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$OutboxEntriesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$OutboxEntriesTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> targetTable = const Value.absent(),
                Value<String> recordId = const Value.absent(),
                Value<String> operation = const Value.absent(),
                Value<String?> payloadJson = const Value.absent(),
                Value<DateTime> clientUpdatedAt = const Value.absent(),
                Value<DateTime> createdAt = const Value.absent(),
                Value<int> attempts = const Value.absent(),
                Value<String?> lastError = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => OutboxEntriesCompanion(
                id: id,
                targetTable: targetTable,
                recordId: recordId,
                operation: operation,
                payloadJson: payloadJson,
                clientUpdatedAt: clientUpdatedAt,
                createdAt: createdAt,
                attempts: attempts,
                lastError: lastError,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String targetTable,
                required String recordId,
                required String operation,
                Value<String?> payloadJson = const Value.absent(),
                required DateTime clientUpdatedAt,
                required DateTime createdAt,
                Value<int> attempts = const Value.absent(),
                Value<String?> lastError = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => OutboxEntriesCompanion.insert(
                id: id,
                targetTable: targetTable,
                recordId: recordId,
                operation: operation,
                payloadJson: payloadJson,
                clientUpdatedAt: clientUpdatedAt,
                createdAt: createdAt,
                attempts: attempts,
                lastError: lastError,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$OutboxEntriesTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $OutboxEntriesTable,
      OutboxEntry,
      $$OutboxEntriesTableFilterComposer,
      $$OutboxEntriesTableOrderingComposer,
      $$OutboxEntriesTableAnnotationComposer,
      $$OutboxEntriesTableCreateCompanionBuilder,
      $$OutboxEntriesTableUpdateCompanionBuilder,
      (
        OutboxEntry,
        BaseReferences<_$AppDatabase, $OutboxEntriesTable, OutboxEntry>,
      ),
      OutboxEntry,
      PrefetchHooks Function()
    >;
typedef $$SyncMetadataTableCreateCompanionBuilder =
    SyncMetadataCompanion Function({
      required String key,
      required String value,
      Value<int> rowid,
    });
typedef $$SyncMetadataTableUpdateCompanionBuilder =
    SyncMetadataCompanion Function({
      Value<String> key,
      Value<String> value,
      Value<int> rowid,
    });

class $$SyncMetadataTableFilterComposer
    extends Composer<_$AppDatabase, $SyncMetadataTable> {
  $$SyncMetadataTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get key => $composableBuilder(
    column: $table.key,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get value => $composableBuilder(
    column: $table.value,
    builder: (column) => ColumnFilters(column),
  );
}

class $$SyncMetadataTableOrderingComposer
    extends Composer<_$AppDatabase, $SyncMetadataTable> {
  $$SyncMetadataTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get key => $composableBuilder(
    column: $table.key,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get value => $composableBuilder(
    column: $table.value,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$SyncMetadataTableAnnotationComposer
    extends Composer<_$AppDatabase, $SyncMetadataTable> {
  $$SyncMetadataTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get key =>
      $composableBuilder(column: $table.key, builder: (column) => column);

  GeneratedColumn<String> get value =>
      $composableBuilder(column: $table.value, builder: (column) => column);
}

class $$SyncMetadataTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $SyncMetadataTable,
          SyncMetadataData,
          $$SyncMetadataTableFilterComposer,
          $$SyncMetadataTableOrderingComposer,
          $$SyncMetadataTableAnnotationComposer,
          $$SyncMetadataTableCreateCompanionBuilder,
          $$SyncMetadataTableUpdateCompanionBuilder,
          (
            SyncMetadataData,
            BaseReferences<_$AppDatabase, $SyncMetadataTable, SyncMetadataData>,
          ),
          SyncMetadataData,
          PrefetchHooks Function()
        > {
  $$SyncMetadataTableTableManager(_$AppDatabase db, $SyncMetadataTable table)
    : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$SyncMetadataTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$SyncMetadataTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$SyncMetadataTableAnnotationComposer($db: db, $table: table),
          updateCompanionCallback:
              ({
                Value<String> key = const Value.absent(),
                Value<String> value = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => SyncMetadataCompanion(key: key, value: value, rowid: rowid),
          createCompanionCallback:
              ({
                required String key,
                required String value,
                Value<int> rowid = const Value.absent(),
              }) => SyncMetadataCompanion.insert(
                key: key,
                value: value,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$SyncMetadataTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $SyncMetadataTable,
      SyncMetadataData,
      $$SyncMetadataTableFilterComposer,
      $$SyncMetadataTableOrderingComposer,
      $$SyncMetadataTableAnnotationComposer,
      $$SyncMetadataTableCreateCompanionBuilder,
      $$SyncMetadataTableUpdateCompanionBuilder,
      (
        SyncMetadataData,
        BaseReferences<_$AppDatabase, $SyncMetadataTable, SyncMetadataData>,
      ),
      SyncMetadataData,
      PrefetchHooks Function()
    >;
typedef $$DesktopWorkspacesTableCreateCompanionBuilder =
    DesktopWorkspacesCompanion Function({
      required String id,
      required String rootPath,
      required String displayName,
      required DateTime lastOpenedAt,
      Value<int> rowid,
    });
typedef $$DesktopWorkspacesTableUpdateCompanionBuilder =
    DesktopWorkspacesCompanion Function({
      Value<String> id,
      Value<String> rootPath,
      Value<String> displayName,
      Value<DateTime> lastOpenedAt,
      Value<int> rowid,
    });

class $$DesktopWorkspacesTableFilterComposer
    extends Composer<_$AppDatabase, $DesktopWorkspacesTable> {
  $$DesktopWorkspacesTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get rootPath => $composableBuilder(
    column: $table.rootPath,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get displayName => $composableBuilder(
    column: $table.displayName,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get lastOpenedAt => $composableBuilder(
    column: $table.lastOpenedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$DesktopWorkspacesTableOrderingComposer
    extends Composer<_$AppDatabase, $DesktopWorkspacesTable> {
  $$DesktopWorkspacesTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get id => $composableBuilder(
    column: $table.id,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get rootPath => $composableBuilder(
    column: $table.rootPath,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get displayName => $composableBuilder(
    column: $table.displayName,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get lastOpenedAt => $composableBuilder(
    column: $table.lastOpenedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$DesktopWorkspacesTableAnnotationComposer
    extends Composer<_$AppDatabase, $DesktopWorkspacesTable> {
  $$DesktopWorkspacesTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get id =>
      $composableBuilder(column: $table.id, builder: (column) => column);

  GeneratedColumn<String> get rootPath =>
      $composableBuilder(column: $table.rootPath, builder: (column) => column);

  GeneratedColumn<String> get displayName => $composableBuilder(
    column: $table.displayName,
    builder: (column) => column,
  );

  GeneratedColumn<DateTime> get lastOpenedAt => $composableBuilder(
    column: $table.lastOpenedAt,
    builder: (column) => column,
  );
}

class $$DesktopWorkspacesTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $DesktopWorkspacesTable,
          DesktopWorkspace,
          $$DesktopWorkspacesTableFilterComposer,
          $$DesktopWorkspacesTableOrderingComposer,
          $$DesktopWorkspacesTableAnnotationComposer,
          $$DesktopWorkspacesTableCreateCompanionBuilder,
          $$DesktopWorkspacesTableUpdateCompanionBuilder,
          (
            DesktopWorkspace,
            BaseReferences<
              _$AppDatabase,
              $DesktopWorkspacesTable,
              DesktopWorkspace
            >,
          ),
          DesktopWorkspace,
          PrefetchHooks Function()
        > {
  $$DesktopWorkspacesTableTableManager(
    _$AppDatabase db,
    $DesktopWorkspacesTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$DesktopWorkspacesTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$DesktopWorkspacesTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$DesktopWorkspacesTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> id = const Value.absent(),
                Value<String> rootPath = const Value.absent(),
                Value<String> displayName = const Value.absent(),
                Value<DateTime> lastOpenedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopWorkspacesCompanion(
                id: id,
                rootPath: rootPath,
                displayName: displayName,
                lastOpenedAt: lastOpenedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String id,
                required String rootPath,
                required String displayName,
                required DateTime lastOpenedAt,
                Value<int> rowid = const Value.absent(),
              }) => DesktopWorkspacesCompanion.insert(
                id: id,
                rootPath: rootPath,
                displayName: displayName,
                lastOpenedAt: lastOpenedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$DesktopWorkspacesTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $DesktopWorkspacesTable,
      DesktopWorkspace,
      $$DesktopWorkspacesTableFilterComposer,
      $$DesktopWorkspacesTableOrderingComposer,
      $$DesktopWorkspacesTableAnnotationComposer,
      $$DesktopWorkspacesTableCreateCompanionBuilder,
      $$DesktopWorkspacesTableUpdateCompanionBuilder,
      (
        DesktopWorkspace,
        BaseReferences<
          _$AppDatabase,
          $DesktopWorkspacesTable,
          DesktopWorkspace
        >,
      ),
      DesktopWorkspace,
      PrefetchHooks Function()
    >;
typedef $$DesktopEditorStateTableCreateCompanionBuilder =
    DesktopEditorStateCompanion Function({
      required String workspaceId,
      required String openTabsJson,
      Value<String?> activeTabPath,
      Value<double?> sidebarWidth,
      Value<double?> agentPanelWidth,
      Value<double?> bottomPanelHeight,
      Value<int> rowid,
    });
typedef $$DesktopEditorStateTableUpdateCompanionBuilder =
    DesktopEditorStateCompanion Function({
      Value<String> workspaceId,
      Value<String> openTabsJson,
      Value<String?> activeTabPath,
      Value<double?> sidebarWidth,
      Value<double?> agentPanelWidth,
      Value<double?> bottomPanelHeight,
      Value<int> rowid,
    });

class $$DesktopEditorStateTableFilterComposer
    extends Composer<_$AppDatabase, $DesktopEditorStateTable> {
  $$DesktopEditorStateTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get openTabsJson => $composableBuilder(
    column: $table.openTabsJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get activeTabPath => $composableBuilder(
    column: $table.activeTabPath,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get sidebarWidth => $composableBuilder(
    column: $table.sidebarWidth,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get agentPanelWidth => $composableBuilder(
    column: $table.agentPanelWidth,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<double> get bottomPanelHeight => $composableBuilder(
    column: $table.bottomPanelHeight,
    builder: (column) => ColumnFilters(column),
  );
}

class $$DesktopEditorStateTableOrderingComposer
    extends Composer<_$AppDatabase, $DesktopEditorStateTable> {
  $$DesktopEditorStateTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get openTabsJson => $composableBuilder(
    column: $table.openTabsJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get activeTabPath => $composableBuilder(
    column: $table.activeTabPath,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get sidebarWidth => $composableBuilder(
    column: $table.sidebarWidth,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get agentPanelWidth => $composableBuilder(
    column: $table.agentPanelWidth,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<double> get bottomPanelHeight => $composableBuilder(
    column: $table.bottomPanelHeight,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$DesktopEditorStateTableAnnotationComposer
    extends Composer<_$AppDatabase, $DesktopEditorStateTable> {
  $$DesktopEditorStateTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get openTabsJson => $composableBuilder(
    column: $table.openTabsJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get activeTabPath => $composableBuilder(
    column: $table.activeTabPath,
    builder: (column) => column,
  );

  GeneratedColumn<double> get sidebarWidth => $composableBuilder(
    column: $table.sidebarWidth,
    builder: (column) => column,
  );

  GeneratedColumn<double> get agentPanelWidth => $composableBuilder(
    column: $table.agentPanelWidth,
    builder: (column) => column,
  );

  GeneratedColumn<double> get bottomPanelHeight => $composableBuilder(
    column: $table.bottomPanelHeight,
    builder: (column) => column,
  );
}

class $$DesktopEditorStateTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $DesktopEditorStateTable,
          DesktopEditorStateData,
          $$DesktopEditorStateTableFilterComposer,
          $$DesktopEditorStateTableOrderingComposer,
          $$DesktopEditorStateTableAnnotationComposer,
          $$DesktopEditorStateTableCreateCompanionBuilder,
          $$DesktopEditorStateTableUpdateCompanionBuilder,
          (
            DesktopEditorStateData,
            BaseReferences<
              _$AppDatabase,
              $DesktopEditorStateTable,
              DesktopEditorStateData
            >,
          ),
          DesktopEditorStateData,
          PrefetchHooks Function()
        > {
  $$DesktopEditorStateTableTableManager(
    _$AppDatabase db,
    $DesktopEditorStateTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$DesktopEditorStateTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$DesktopEditorStateTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$DesktopEditorStateTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> workspaceId = const Value.absent(),
                Value<String> openTabsJson = const Value.absent(),
                Value<String?> activeTabPath = const Value.absent(),
                Value<double?> sidebarWidth = const Value.absent(),
                Value<double?> agentPanelWidth = const Value.absent(),
                Value<double?> bottomPanelHeight = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopEditorStateCompanion(
                workspaceId: workspaceId,
                openTabsJson: openTabsJson,
                activeTabPath: activeTabPath,
                sidebarWidth: sidebarWidth,
                agentPanelWidth: agentPanelWidth,
                bottomPanelHeight: bottomPanelHeight,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String workspaceId,
                required String openTabsJson,
                Value<String?> activeTabPath = const Value.absent(),
                Value<double?> sidebarWidth = const Value.absent(),
                Value<double?> agentPanelWidth = const Value.absent(),
                Value<double?> bottomPanelHeight = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopEditorStateCompanion.insert(
                workspaceId: workspaceId,
                openTabsJson: openTabsJson,
                activeTabPath: activeTabPath,
                sidebarWidth: sidebarWidth,
                agentPanelWidth: agentPanelWidth,
                bottomPanelHeight: bottomPanelHeight,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$DesktopEditorStateTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $DesktopEditorStateTable,
      DesktopEditorStateData,
      $$DesktopEditorStateTableFilterComposer,
      $$DesktopEditorStateTableOrderingComposer,
      $$DesktopEditorStateTableAnnotationComposer,
      $$DesktopEditorStateTableCreateCompanionBuilder,
      $$DesktopEditorStateTableUpdateCompanionBuilder,
      (
        DesktopEditorStateData,
        BaseReferences<
          _$AppDatabase,
          $DesktopEditorStateTable,
          DesktopEditorStateData
        >,
      ),
      DesktopEditorStateData,
      PrefetchHooks Function()
    >;
typedef $$DesktopTerminalStateTableCreateCompanionBuilder =
    DesktopTerminalStateCompanion Function({
      required String workspaceId,
      required String sessionsJson,
      Value<int> rowid,
    });
typedef $$DesktopTerminalStateTableUpdateCompanionBuilder =
    DesktopTerminalStateCompanion Function({
      Value<String> workspaceId,
      Value<String> sessionsJson,
      Value<int> rowid,
    });

class $$DesktopTerminalStateTableFilterComposer
    extends Composer<_$AppDatabase, $DesktopTerminalStateTable> {
  $$DesktopTerminalStateTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get sessionsJson => $composableBuilder(
    column: $table.sessionsJson,
    builder: (column) => ColumnFilters(column),
  );
}

class $$DesktopTerminalStateTableOrderingComposer
    extends Composer<_$AppDatabase, $DesktopTerminalStateTable> {
  $$DesktopTerminalStateTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get sessionsJson => $composableBuilder(
    column: $table.sessionsJson,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$DesktopTerminalStateTableAnnotationComposer
    extends Composer<_$AppDatabase, $DesktopTerminalStateTable> {
  $$DesktopTerminalStateTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get sessionsJson => $composableBuilder(
    column: $table.sessionsJson,
    builder: (column) => column,
  );
}

class $$DesktopTerminalStateTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $DesktopTerminalStateTable,
          DesktopTerminalStateData,
          $$DesktopTerminalStateTableFilterComposer,
          $$DesktopTerminalStateTableOrderingComposer,
          $$DesktopTerminalStateTableAnnotationComposer,
          $$DesktopTerminalStateTableCreateCompanionBuilder,
          $$DesktopTerminalStateTableUpdateCompanionBuilder,
          (
            DesktopTerminalStateData,
            BaseReferences<
              _$AppDatabase,
              $DesktopTerminalStateTable,
              DesktopTerminalStateData
            >,
          ),
          DesktopTerminalStateData,
          PrefetchHooks Function()
        > {
  $$DesktopTerminalStateTableTableManager(
    _$AppDatabase db,
    $DesktopTerminalStateTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$DesktopTerminalStateTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$DesktopTerminalStateTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$DesktopTerminalStateTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> workspaceId = const Value.absent(),
                Value<String> sessionsJson = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopTerminalStateCompanion(
                workspaceId: workspaceId,
                sessionsJson: sessionsJson,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String workspaceId,
                required String sessionsJson,
                Value<int> rowid = const Value.absent(),
              }) => DesktopTerminalStateCompanion.insert(
                workspaceId: workspaceId,
                sessionsJson: sessionsJson,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$DesktopTerminalStateTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $DesktopTerminalStateTable,
      DesktopTerminalStateData,
      $$DesktopTerminalStateTableFilterComposer,
      $$DesktopTerminalStateTableOrderingComposer,
      $$DesktopTerminalStateTableAnnotationComposer,
      $$DesktopTerminalStateTableCreateCompanionBuilder,
      $$DesktopTerminalStateTableUpdateCompanionBuilder,
      (
        DesktopTerminalStateData,
        BaseReferences<
          _$AppDatabase,
          $DesktopTerminalStateTable,
          DesktopTerminalStateData
        >,
      ),
      DesktopTerminalStateData,
      PrefetchHooks Function()
    >;
typedef $$DesktopLayoutStateTableCreateCompanionBuilder =
    DesktopLayoutStateCompanion Function({
      required String workspaceId,
      required String windowGeometryJson,
      required String panelVisibilityJson,
      Value<int> rowid,
    });
typedef $$DesktopLayoutStateTableUpdateCompanionBuilder =
    DesktopLayoutStateCompanion Function({
      Value<String> workspaceId,
      Value<String> windowGeometryJson,
      Value<String> panelVisibilityJson,
      Value<int> rowid,
    });

class $$DesktopLayoutStateTableFilterComposer
    extends Composer<_$AppDatabase, $DesktopLayoutStateTable> {
  $$DesktopLayoutStateTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get windowGeometryJson => $composableBuilder(
    column: $table.windowGeometryJson,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get panelVisibilityJson => $composableBuilder(
    column: $table.panelVisibilityJson,
    builder: (column) => ColumnFilters(column),
  );
}

class $$DesktopLayoutStateTableOrderingComposer
    extends Composer<_$AppDatabase, $DesktopLayoutStateTable> {
  $$DesktopLayoutStateTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get windowGeometryJson => $composableBuilder(
    column: $table.windowGeometryJson,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get panelVisibilityJson => $composableBuilder(
    column: $table.panelVisibilityJson,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$DesktopLayoutStateTableAnnotationComposer
    extends Composer<_$AppDatabase, $DesktopLayoutStateTable> {
  $$DesktopLayoutStateTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get windowGeometryJson => $composableBuilder(
    column: $table.windowGeometryJson,
    builder: (column) => column,
  );

  GeneratedColumn<String> get panelVisibilityJson => $composableBuilder(
    column: $table.panelVisibilityJson,
    builder: (column) => column,
  );
}

class $$DesktopLayoutStateTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $DesktopLayoutStateTable,
          DesktopLayoutStateData,
          $$DesktopLayoutStateTableFilterComposer,
          $$DesktopLayoutStateTableOrderingComposer,
          $$DesktopLayoutStateTableAnnotationComposer,
          $$DesktopLayoutStateTableCreateCompanionBuilder,
          $$DesktopLayoutStateTableUpdateCompanionBuilder,
          (
            DesktopLayoutStateData,
            BaseReferences<
              _$AppDatabase,
              $DesktopLayoutStateTable,
              DesktopLayoutStateData
            >,
          ),
          DesktopLayoutStateData,
          PrefetchHooks Function()
        > {
  $$DesktopLayoutStateTableTableManager(
    _$AppDatabase db,
    $DesktopLayoutStateTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$DesktopLayoutStateTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$DesktopLayoutStateTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$DesktopLayoutStateTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> workspaceId = const Value.absent(),
                Value<String> windowGeometryJson = const Value.absent(),
                Value<String> panelVisibilityJson = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopLayoutStateCompanion(
                workspaceId: workspaceId,
                windowGeometryJson: windowGeometryJson,
                panelVisibilityJson: panelVisibilityJson,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String workspaceId,
                required String windowGeometryJson,
                required String panelVisibilityJson,
                Value<int> rowid = const Value.absent(),
              }) => DesktopLayoutStateCompanion.insert(
                workspaceId: workspaceId,
                windowGeometryJson: windowGeometryJson,
                panelVisibilityJson: panelVisibilityJson,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$DesktopLayoutStateTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $DesktopLayoutStateTable,
      DesktopLayoutStateData,
      $$DesktopLayoutStateTableFilterComposer,
      $$DesktopLayoutStateTableOrderingComposer,
      $$DesktopLayoutStateTableAnnotationComposer,
      $$DesktopLayoutStateTableCreateCompanionBuilder,
      $$DesktopLayoutStateTableUpdateCompanionBuilder,
      (
        DesktopLayoutStateData,
        BaseReferences<
          _$AppDatabase,
          $DesktopLayoutStateTable,
          DesktopLayoutStateData
        >,
      ),
      DesktopLayoutStateData,
      PrefetchHooks Function()
    >;
typedef $$DesktopAgentStateTableCreateCompanionBuilder =
    DesktopAgentStateCompanion Function({
      required String workspaceId,
      required String agentStateJson,
      Value<int> rowid,
    });
typedef $$DesktopAgentStateTableUpdateCompanionBuilder =
    DesktopAgentStateCompanion Function({
      Value<String> workspaceId,
      Value<String> agentStateJson,
      Value<int> rowid,
    });

class $$DesktopAgentStateTableFilterComposer
    extends Composer<_$AppDatabase, $DesktopAgentStateTable> {
  $$DesktopAgentStateTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get agentStateJson => $composableBuilder(
    column: $table.agentStateJson,
    builder: (column) => ColumnFilters(column),
  );
}

class $$DesktopAgentStateTableOrderingComposer
    extends Composer<_$AppDatabase, $DesktopAgentStateTable> {
  $$DesktopAgentStateTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get agentStateJson => $composableBuilder(
    column: $table.agentStateJson,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$DesktopAgentStateTableAnnotationComposer
    extends Composer<_$AppDatabase, $DesktopAgentStateTable> {
  $$DesktopAgentStateTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get agentStateJson => $composableBuilder(
    column: $table.agentStateJson,
    builder: (column) => column,
  );
}

class $$DesktopAgentStateTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $DesktopAgentStateTable,
          DesktopAgentStateData,
          $$DesktopAgentStateTableFilterComposer,
          $$DesktopAgentStateTableOrderingComposer,
          $$DesktopAgentStateTableAnnotationComposer,
          $$DesktopAgentStateTableCreateCompanionBuilder,
          $$DesktopAgentStateTableUpdateCompanionBuilder,
          (
            DesktopAgentStateData,
            BaseReferences<
              _$AppDatabase,
              $DesktopAgentStateTable,
              DesktopAgentStateData
            >,
          ),
          DesktopAgentStateData,
          PrefetchHooks Function()
        > {
  $$DesktopAgentStateTableTableManager(
    _$AppDatabase db,
    $DesktopAgentStateTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$DesktopAgentStateTableFilterComposer($db: db, $table: table),
          createOrderingComposer: () =>
              $$DesktopAgentStateTableOrderingComposer($db: db, $table: table),
          createComputedFieldComposer: () =>
              $$DesktopAgentStateTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> workspaceId = const Value.absent(),
                Value<String> agentStateJson = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopAgentStateCompanion(
                workspaceId: workspaceId,
                agentStateJson: agentStateJson,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String workspaceId,
                required String agentStateJson,
                Value<int> rowid = const Value.absent(),
              }) => DesktopAgentStateCompanion.insert(
                workspaceId: workspaceId,
                agentStateJson: agentStateJson,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$DesktopAgentStateTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $DesktopAgentStateTable,
      DesktopAgentStateData,
      $$DesktopAgentStateTableFilterComposer,
      $$DesktopAgentStateTableOrderingComposer,
      $$DesktopAgentStateTableAnnotationComposer,
      $$DesktopAgentStateTableCreateCompanionBuilder,
      $$DesktopAgentStateTableUpdateCompanionBuilder,
      (
        DesktopAgentStateData,
        BaseReferences<
          _$AppDatabase,
          $DesktopAgentStateTable,
          DesktopAgentStateData
        >,
      ),
      DesktopAgentStateData,
      PrefetchHooks Function()
    >;
typedef $$DesktopUnsavedBuffersTableCreateCompanionBuilder =
    DesktopUnsavedBuffersCompanion Function({
      required String workspaceId,
      required String tabId,
      required String path,
      required String contentEnc,
      Value<String?> baseHash,
      required DateTime updatedAt,
      Value<int> rowid,
    });
typedef $$DesktopUnsavedBuffersTableUpdateCompanionBuilder =
    DesktopUnsavedBuffersCompanion Function({
      Value<String> workspaceId,
      Value<String> tabId,
      Value<String> path,
      Value<String> contentEnc,
      Value<String?> baseHash,
      Value<DateTime> updatedAt,
      Value<int> rowid,
    });

class $$DesktopUnsavedBuffersTableFilterComposer
    extends Composer<_$AppDatabase, $DesktopUnsavedBuffersTable> {
  $$DesktopUnsavedBuffersTableFilterComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnFilters<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get tabId => $composableBuilder(
    column: $table.tabId,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get path => $composableBuilder(
    column: $table.path,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get contentEnc => $composableBuilder(
    column: $table.contentEnc,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<String> get baseHash => $composableBuilder(
    column: $table.baseHash,
    builder: (column) => ColumnFilters(column),
  );

  ColumnFilters<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnFilters(column),
  );
}

class $$DesktopUnsavedBuffersTableOrderingComposer
    extends Composer<_$AppDatabase, $DesktopUnsavedBuffersTable> {
  $$DesktopUnsavedBuffersTableOrderingComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  ColumnOrderings<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get tabId => $composableBuilder(
    column: $table.tabId,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get path => $composableBuilder(
    column: $table.path,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get contentEnc => $composableBuilder(
    column: $table.contentEnc,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<String> get baseHash => $composableBuilder(
    column: $table.baseHash,
    builder: (column) => ColumnOrderings(column),
  );

  ColumnOrderings<DateTime> get updatedAt => $composableBuilder(
    column: $table.updatedAt,
    builder: (column) => ColumnOrderings(column),
  );
}

class $$DesktopUnsavedBuffersTableAnnotationComposer
    extends Composer<_$AppDatabase, $DesktopUnsavedBuffersTable> {
  $$DesktopUnsavedBuffersTableAnnotationComposer({
    required super.$db,
    required super.$table,
    super.joinBuilder,
    super.$addJoinBuilderToRootComposer,
    super.$removeJoinBuilderFromRootComposer,
  });
  GeneratedColumn<String> get workspaceId => $composableBuilder(
    column: $table.workspaceId,
    builder: (column) => column,
  );

  GeneratedColumn<String> get tabId =>
      $composableBuilder(column: $table.tabId, builder: (column) => column);

  GeneratedColumn<String> get path =>
      $composableBuilder(column: $table.path, builder: (column) => column);

  GeneratedColumn<String> get contentEnc => $composableBuilder(
    column: $table.contentEnc,
    builder: (column) => column,
  );

  GeneratedColumn<String> get baseHash =>
      $composableBuilder(column: $table.baseHash, builder: (column) => column);

  GeneratedColumn<DateTime> get updatedAt =>
      $composableBuilder(column: $table.updatedAt, builder: (column) => column);
}

class $$DesktopUnsavedBuffersTableTableManager
    extends
        RootTableManager<
          _$AppDatabase,
          $DesktopUnsavedBuffersTable,
          DesktopUnsavedBuffer,
          $$DesktopUnsavedBuffersTableFilterComposer,
          $$DesktopUnsavedBuffersTableOrderingComposer,
          $$DesktopUnsavedBuffersTableAnnotationComposer,
          $$DesktopUnsavedBuffersTableCreateCompanionBuilder,
          $$DesktopUnsavedBuffersTableUpdateCompanionBuilder,
          (
            DesktopUnsavedBuffer,
            BaseReferences<
              _$AppDatabase,
              $DesktopUnsavedBuffersTable,
              DesktopUnsavedBuffer
            >,
          ),
          DesktopUnsavedBuffer,
          PrefetchHooks Function()
        > {
  $$DesktopUnsavedBuffersTableTableManager(
    _$AppDatabase db,
    $DesktopUnsavedBuffersTable table,
  ) : super(
        TableManagerState(
          db: db,
          table: table,
          createFilteringComposer: () =>
              $$DesktopUnsavedBuffersTableFilterComposer(
                $db: db,
                $table: table,
              ),
          createOrderingComposer: () =>
              $$DesktopUnsavedBuffersTableOrderingComposer(
                $db: db,
                $table: table,
              ),
          createComputedFieldComposer: () =>
              $$DesktopUnsavedBuffersTableAnnotationComposer(
                $db: db,
                $table: table,
              ),
          updateCompanionCallback:
              ({
                Value<String> workspaceId = const Value.absent(),
                Value<String> tabId = const Value.absent(),
                Value<String> path = const Value.absent(),
                Value<String> contentEnc = const Value.absent(),
                Value<String?> baseHash = const Value.absent(),
                Value<DateTime> updatedAt = const Value.absent(),
                Value<int> rowid = const Value.absent(),
              }) => DesktopUnsavedBuffersCompanion(
                workspaceId: workspaceId,
                tabId: tabId,
                path: path,
                contentEnc: contentEnc,
                baseHash: baseHash,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          createCompanionCallback:
              ({
                required String workspaceId,
                required String tabId,
                required String path,
                required String contentEnc,
                Value<String?> baseHash = const Value.absent(),
                required DateTime updatedAt,
                Value<int> rowid = const Value.absent(),
              }) => DesktopUnsavedBuffersCompanion.insert(
                workspaceId: workspaceId,
                tabId: tabId,
                path: path,
                contentEnc: contentEnc,
                baseHash: baseHash,
                updatedAt: updatedAt,
                rowid: rowid,
              ),
          withReferenceMapper: (p0) => p0
              .map((e) => (e.readTable(table), BaseReferences(db, table, e)))
              .toList(),
          prefetchHooksCallback: null,
        ),
      );
}

typedef $$DesktopUnsavedBuffersTableProcessedTableManager =
    ProcessedTableManager<
      _$AppDatabase,
      $DesktopUnsavedBuffersTable,
      DesktopUnsavedBuffer,
      $$DesktopUnsavedBuffersTableFilterComposer,
      $$DesktopUnsavedBuffersTableOrderingComposer,
      $$DesktopUnsavedBuffersTableAnnotationComposer,
      $$DesktopUnsavedBuffersTableCreateCompanionBuilder,
      $$DesktopUnsavedBuffersTableUpdateCompanionBuilder,
      (
        DesktopUnsavedBuffer,
        BaseReferences<
          _$AppDatabase,
          $DesktopUnsavedBuffersTable,
          DesktopUnsavedBuffer
        >,
      ),
      DesktopUnsavedBuffer,
      PrefetchHooks Function()
    >;

class $AppDatabaseManager {
  final _$AppDatabase _db;
  $AppDatabaseManager(this._db);
  $$TasksTableTableManager get tasks =>
      $$TasksTableTableManager(_db, _db.tasks);
  $$CalendarEventsTableTableManager get calendarEvents =>
      $$CalendarEventsTableTableManager(_db, _db.calendarEvents);
  $$FinanceRecordsTableTableManager get financeRecords =>
      $$FinanceRecordsTableTableManager(_db, _db.financeRecords);
  $$HealthRecordsTableTableManager get healthRecords =>
      $$HealthRecordsTableTableManager(_db, _db.healthRecords);
  $$MessagesTableTableManager get messages =>
      $$MessagesTableTableManager(_db, _db.messages);
  $$AgentConfigurationsTableTableManager get agentConfigurations =>
      $$AgentConfigurationsTableTableManager(_db, _db.agentConfigurations);
  $$UserMemoriesTableTableManager get userMemories =>
      $$UserMemoriesTableTableManager(_db, _db.userMemories);
  $$ProjectsTableTableManager get projects =>
      $$ProjectsTableTableManager(_db, _db.projects);
  $$GoalsTableTableManager get goals =>
      $$GoalsTableTableManager(_db, _db.goals);
  $$PendingDeletesTableTableManager get pendingDeletes =>
      $$PendingDeletesTableTableManager(_db, _db.pendingDeletes);
  $$LearningLogsTableTableManager get learningLogs =>
      $$LearningLogsTableTableManager(_db, _db.learningLogs);
  $$OfflineModelWeightsTableTableManager get offlineModelWeights =>
      $$OfflineModelWeightsTableTableManager(_db, _db.offlineModelWeights);
  $$TelemetryLogsTableTableManager get telemetryLogs =>
      $$TelemetryLogsTableTableManager(_db, _db.telemetryLogs);
  $$OutboxEntriesTableTableManager get outboxEntries =>
      $$OutboxEntriesTableTableManager(_db, _db.outboxEntries);
  $$SyncMetadataTableTableManager get syncMetadata =>
      $$SyncMetadataTableTableManager(_db, _db.syncMetadata);
  $$DesktopWorkspacesTableTableManager get desktopWorkspaces =>
      $$DesktopWorkspacesTableTableManager(_db, _db.desktopWorkspaces);
  $$DesktopEditorStateTableTableManager get desktopEditorState =>
      $$DesktopEditorStateTableTableManager(_db, _db.desktopEditorState);
  $$DesktopTerminalStateTableTableManager get desktopTerminalState =>
      $$DesktopTerminalStateTableTableManager(_db, _db.desktopTerminalState);
  $$DesktopLayoutStateTableTableManager get desktopLayoutState =>
      $$DesktopLayoutStateTableTableManager(_db, _db.desktopLayoutState);
  $$DesktopAgentStateTableTableManager get desktopAgentState =>
      $$DesktopAgentStateTableTableManager(_db, _db.desktopAgentState);
  $$DesktopUnsavedBuffersTableTableManager get desktopUnsavedBuffers =>
      $$DesktopUnsavedBuffersTableTableManager(_db, _db.desktopUnsavedBuffers);
}
