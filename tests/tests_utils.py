import pytest
from asgiref.sync import sync_to_async
from channels.testing import WebsocketCommunicator

from realtime_api.testing import AuthWebsocketCommunicator, create_user
from realtime_api.utils import get_group_user_key, close_user_channels

from .consumers import GroupTestConsumer


def test_group_does_not_exist():
    assert close_user_channels('some-pk') is None


def test_get_group_user_name():
    assert get_group_user_key('') == '.user'
    assert get_group_user_key(35) == '.user35'
    assert get_group_user_key('key') == '.userkey'


@pytest.mark.asyncio
async def test_close_user_channels_authenticated(user):
    communicator = await AuthWebsocketCommunicator(
        GroupTestConsumer,
        '/',
        user=user,
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await sync_to_async(close_user_channels)(user.pk)
    response = await communicator.receive_from()
    assert response == 'Socket closes with code 3'


@pytest.mark.asyncio
async def test_close_user_channels_no_user():
    communicator = WebsocketCommunicator(GroupTestConsumer, '/')
    connected, subprotocol = await communicator.connect()
    assert connected
    await sync_to_async(close_user_channels)('')
    response = await communicator.receive_from()
    assert response == 'Socket closes with code 3'


@pytest.mark.asyncio
async def test_close_user_channels_multiple_connections(user):
    user2 = await create_user('Simon')
    user3 = await create_user('Mary')
    c_u11 = await AuthWebsocketCommunicator(GroupTestConsumer, '/', user=user)
    c_u12 = await AuthWebsocketCommunicator(GroupTestConsumer, '/', user=user)
    c_u13 = await AuthWebsocketCommunicator(GroupTestConsumer, 't', user=user)
    c_u21 = await AuthWebsocketCommunicator(GroupTestConsumer, '/', user=user2)
    c_u22 = await AuthWebsocketCommunicator(GroupTestConsumer, '/', user=user2)
    c_u31 = await AuthWebsocketCommunicator(GroupTestConsumer, 't', user=user3)
    c_u32 = await AuthWebsocketCommunicator(GroupTestConsumer, 't', user=user3)
    c_n1 = await AuthWebsocketCommunicator(GroupTestConsumer, '/')
    c_n2 = WebsocketCommunicator(GroupTestConsumer, '/')
    c_n3 = WebsocketCommunicator(GroupTestConsumer, 't')
    connected, subprotocol = await c_u11.connect()
    connected, subprotocol = await c_u12.connect()
    connected, subprotocol = await c_u13.connect()
    connected, subprotocol = await c_u21.connect()
    connected, subprotocol = await c_u22.connect()
    connected, subprotocol = await c_u31.connect()
    connected, subprotocol = await c_u32.connect()
    connected, subprotocol = await c_n1.connect()
    connected, subprotocol = await c_n2.connect()
    connected, subprotocol = await c_n3.connect()

    await sync_to_async(close_user_channels)(user.pk)
    assert not await c_u11.receive_nothing(0.02)
    assert not await c_u12.receive_nothing(0.02)
    assert not await c_u13.receive_nothing(0.02)
    assert await c_u21.receive_nothing(0.02)
    assert await c_u22.receive_nothing(0.02)
    assert await c_u31.receive_nothing(0.02)
    assert await c_u32.receive_nothing(0.02)
    assert await c_n1.receive_nothing(0.02)
    assert await c_n2.receive_nothing(0.02)
    assert await c_n3.receive_nothing(0.02)
    r1 = await c_u11.receive_from()
    r2 = await c_u12.receive_from()
    r3 = await c_u13.receive_from()
    assert r1 == r2 == r3 == 'Socket closes with code 3'

    await sync_to_async(close_user_channels)('')
    assert await c_u11.receive_nothing(0.02)
    assert await c_u12.receive_nothing(0.02)
    assert await c_u13.receive_nothing(0.02)
    assert await c_u21.receive_nothing(0.02)
    assert await c_u22.receive_nothing(0.02)
    assert await c_u31.receive_nothing(0.02)
    assert await c_u32.receive_nothing(0.02)
    assert not await c_n1.receive_nothing(0.02)
    assert not await c_n2.receive_nothing(0.02)
    assert not await c_n3.receive_nothing(0.02)
    r1 = await c_n1.receive_from()
    r2 = await c_n2.receive_from()
    r3 = await c_n3.receive_from()
    assert r1 == r2 == r3 == 'Socket closes with code 3'

    await c_u21.disconnect()
    await c_u22.disconnect()
    await c_u31.disconnect()
    await c_u32.disconnect()
