import pytest
from channels.db import database_sync_to_async

from realtime_api.testing import admin, user  # noqa: F401

from . import models


@database_sync_to_async
def create_obj(name=None, counter=None):
    obj = models.APIModel.objects.create(
        name=name or 'Mike',
        counter=counter or 0,
    )
    return obj


# NOTE: This does work so far but the database isn't cleaned up after each test
# in an async context! As a result all modifications to the database are kept.
@pytest.fixture
async def db_obj(db):
    return await create_obj()
