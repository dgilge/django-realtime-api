import pytest
from channels.db import database_sync_to_async

from realtime_api.testing import AuthWebsocketCommunicator, create_user

from .consumers import GroupTestConsumer

pytestmark = pytest.mark.asyncio


async def test_login(user):
    communicator = await AuthWebsocketCommunicator(GroupTestConsumer, '/')
    communicator_authenticated = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user,
    )
    await communicator.connect()
    await communicator_authenticated.connect()

    shirly = await create_user('Shirly')
    # Shirly is simply logged in, not to a specific communicator
    await communicator_authenticated.force_login(shirly)

    response = await communicator.receive_from()
    assert response == 'Socket closes with code 3'
    assert await communicator_authenticated.queue_empty()
    assert await communicator.queue_empty()

    await communicator_authenticated.disconnect()


async def test_logout(user):
    user2 = await create_user()
    communicator = await AuthWebsocketCommunicator(GroupTestConsumer, '/')
    communicator_authenticated = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user,
    )
    communicator_authenticated2 = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user,
    )
    communicator_user2 = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user2,
    )
    await communicator.connect()
    await communicator_authenticated.connect()
    await communicator_authenticated2.connect()
    await communicator_user2.connect()

    await communicator_authenticated.logout()

    r1 = await communicator_authenticated.receive_from()
    r2 = await communicator_authenticated2.receive_from()
    assert r1 == r2 == 'Socket closes with code 3'
    assert await communicator.queue_empty()
    assert await communicator_user2.queue_empty()

    await communicator.disconnect()
    await communicator_user2.disconnect()


@pytest.mark.parametrize('method', ('save', 'delete'))
async def test_user_changes(user, method):
    user2 = await create_user()
    communicator = await AuthWebsocketCommunicator(GroupTestConsumer, '/')
    communicator_authenticated = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user,
    )
    communicator_authenticated2 = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user,
    )
    communicator_user2 = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user2,
    )
    await communicator.connect()
    await communicator_authenticated.connect()
    await communicator_authenticated2.connect()
    await communicator_user2.connect()

    await database_sync_to_async(getattr(user, method))()

    r1 = await communicator_authenticated.receive_from()
    r2 = await communicator_authenticated2.receive_from()
    assert r1 == r2 == 'Socket closes with code 3'
    assert await communicator.queue_empty()
    assert await communicator_user2.queue_empty()

    await communicator.disconnect()
    await communicator_user2.disconnect()
