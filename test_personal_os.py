"""Test personal OS store CRUD."""
import os
os.environ['AURA_ENVIRONMENT'] = 'local'

from aura_core.adapters.memory_stores import InMemoryPersonalOsStore
from aura_core.domain.personal_os import PersonalOsRecord, RecordKind
from aura_core.domain.identifiers import PersonalOsRecordId, UserId
from datetime import datetime

store = InMemoryPersonalOsStore()

# Test putting a record (like create)
record_id = PersonalOsRecordId('test-record-1')
now = datetime.now()
record = PersonalOsRecord(
    id=record_id,
    owner_id=UserId('test-user'),
    kind=RecordKind.TASK,
    fields={'title': 'Test task', 'description': 'A test task'},
    created_at=now,
    updated_at=now,
    source='kernel',
)

# Put the record
store.put(record)
print('Put record successfully')

# Read the record
read_record = store.get(record_id)
print('Got record successfully')

# List records for owner
records = store.list_for_owner(UserId('test-user'))
print('List for owner successfully, count:', len(records))

# Update the record
updated = store.update(record_id, {'title': 'Updated task'})
print('Updated record title:', updated.fields.get('title'))

# Delete the record
store.delete(record_id)
print('Deleted record successfully')

print('\nPersonal OS store CRUD works!')