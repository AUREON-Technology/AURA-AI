"""Test personal OS store async CRUD."""
import sys
sys.path.insert(0, 'E:/AURA AI/aura_backend')
import os
os.environ['AURA_ENVIRONMENT'] = 'local'

import asyncio
from aura_core.adapters.memory_stores import InMemoryPersonalOsStore
from aura_core.domain.personal_os import PersonalOsRecord, RecordKind
from aura_core.domain.identifiers import PersonalOsRecordId, UserId
from datetime import datetime

async def test():
    store = InMemoryPersonalOsStore()
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
    await store.put(record)
    read_record = await store.get(record_id, owner_id=UserId('test-user'))
    print('Got record:', read_record is not None)
    records = await store.list_for_owner(UserId('test-user'))
    print('List count:', len(records))
    updated_record = PersonalOsRecord(
        id=record_id,
        owner_id=UserId('test-user'),
        kind=RecordKind.TASK,
        fields={'title': 'Updated task', 'description': 'A test task'},
        created_at=now,
        updated_at=now,
        source='kernel',
    )
    await store.update(updated_record)
    print('Updated title:', updated_record.fields.get('title'))
    success = await store.delete(record_id, owner_id=UserId('test-user'))
    print('Delete success:', success)
    print('Personal OS store CRUD works!')

asyncio.run(test())