import asyncio
import json
import pytest
from asgiref.sync import sync_to_async
from channels.auth import AuthMiddlewareStack
from channels.db import database_sync_to_async
from channels.exceptions import StopConsumer
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.exceptions import APIException

from realtime_api.consumers import APIDemultiplexer
from realtime_api.testing import AuthWebsocketCommunicator

from . import consumers, models
from .conftest import create_obj


pytestmark = pytest.mark.asyncio

channel_layer = get_channel_layer()


class APITestDemultiplexer(APIDemultiplexer):
    """
    Test demultiplexer with an own registry.
    """
    _registry = {}


APITestDemultiplexer.register(
    consumers.StandardConsumer,
    consumers.MappingConsumer,
    consumers.TestConsumer,
    consumers.ProxyConsumer,
    consumers.PermissionConsumer,
)


def to_json(data):
    return json.dumps(data).encode()


async def api_send(stream, obj):
    group_name = '{}-{}'.format(stream, obj.pk)
    await channel_layer.group_send(
        group_name,
        {
            'type': 'api.send',
            'text': json.dumps({'detail': 'Update notification'}),
        },
    )


@pytest.fixture
async def subscribed_communicator(db_obj):
    communicator = await AuthWebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/subscribe/',
    )
    await communicator.connect()
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    assert response['status'] == 200
    return communicator


# APIConsumer
# ===========

# Signals
# -------

# post_save_receiver

@pytest.mark.django_db
async def test_post_save_receiver_create(subscribed_communicator):
    obj = await create_obj(name='Adam', counter=201)
    response = await subscribed_communicator.receive_json_from()
    assert response['action'] == 'create'
    data = {'id': obj.pk, 'name': 'Adam', 'counter': 201}
    assert response['data'] == data

    await subscribed_communicator.disconnect()


async def test_post_save_receiver_update(db_obj, subscribed_communicator):
    await database_sync_to_async(db_obj.save)()
    response = await subscribed_communicator.receive_json_from()
    assert response['action'] == 'update'
    data = {'id': db_obj.pk, 'name': 'Mike', 'counter': 0}
    assert response['data'] == data

    await subscribed_communicator.disconnect()


async def test_proxy_model_post_save(subscribed_communicator):
    """
    Another model should not send signals to the consumer.
    """
    proxy_communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'proxy/subscribe/',
    )
    await proxy_communicator.connect()
    await proxy_communicator.send_to(bytes_data=to_json(
        {'payload': {'counter': 0}}
    ))
    response = await proxy_communicator.receive_json_from()
    assert response['status'] == 200

    obj = await database_sync_to_async(models.ProxyModel.objects.create)(
        name='Victoria',
        counter=0,
    )
    response = await proxy_communicator.receive_json_from()
    assert response['action'] == 'create'
    data = {'id': obj.pk, 'name': 'Victoria', 'counter': 0}
    assert response['data'] == data
    await proxy_communicator.disconnect()

    assert await subscribed_communicator.queue_empty()
    await subscribed_communicator.disconnect()


# post_delete_receiver

async def test_post_delete_receiver(db_obj, subscribed_communicator):
    pk = db_obj.pk
    await database_sync_to_async(db_obj.delete)()
    response = await subscribed_communicator.receive_json_from()
    assert response['action'] == 'delete'
    assert response['data'] == {'id': pk}

    await subscribed_communicator.disconnect()


@pytest.mark.django_db
async def test_proxy_model_post_delete(subscribed_communicator):
    """
    Another model should not send signals to the consumer.
    """
    obj = await database_sync_to_async(models.ProxyModel.objects.create)(
        name='Alex',
        counter=0,
    )
    proxy_communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'proxy/subscribe/',
    )
    await proxy_communicator.connect()
    await proxy_communicator.send_to(bytes_data=to_json(
        {'payload': {'counter': 0}}
    ))
    response = await proxy_communicator.receive_json_from()
    assert response['status'] == 200

    await database_sync_to_async(obj.delete)()

    response = await proxy_communicator.receive_json_from()
    assert response['action'] == 'delete'
    await proxy_communicator.disconnect()

    assert await subscribed_communicator.queue_empty()
    await subscribed_communicator.disconnect()


# post_change_receiver

async def test_queue_methods():
    communicator = await AuthWebsocketCommunicator(APITestDemultiplexer, '/')
    connected, _ = await communicator.connect()
    assert connected
    assert await communicator.queue_empty() is True
    assert await communicator.queue_count() == 0
    await communicator.disconnect()


@pytest.mark.django_db
async def test_model_attr_signal_broadcast(db_obj, subscribed_communicator):
    await channel_layer.group_send(
        'test-obj',
        {'type': 'api.send', 'text': 'Anybody there?'},
    )
    response = await subscribed_communicator.receive_from()
    assert response == 'Anybody there?'
    await database_sync_to_async(db_obj.save)()
    response = await subscribed_communicator.receive_json_from()
    assert response['action'] == 'update'
    db_obj.signal_broadcast = False
    await database_sync_to_async(db_obj.save)()
    assert await subscribed_communicator.queue_empty()
    await subscribed_communicator.disconnect()


# todo: Test that immediate_broadcast is inactive


# Subscription
# ------------

# subscribe

async def test_subscribe(db_obj):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'standard/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    # TODO should response be bytes?
    expected = {'status': 200, 'text': {'detail': 'subscription successful'}}
    assert response == expected

    await api_send('standard', db_obj)
    response = await communicator.receive_json_from()
    assert response == {'detail': 'Update notification'}

    await communicator.disconnect()


async def test_subscribe_correct_objects(db_obj):
    obj2 = await create_obj(counter=15)
    obj3 = await create_obj(counter=15)
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'standard/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    await communicator.send_to(
        bytes_data=to_json({'payload': {'counter': 15}})
    )
    response = await communicator.receive_from()
    assert response == json.dumps(
        {'status': 200, 'text': {'detail': 'subscription successful'}}
    )

    await api_send('standard', db_obj)
    await asyncio.sleep(0.1)
    assert communicator.output_queue.empty()

    await api_send('standard', obj2)
    r1 = await communicator.receive_json_from()
    await api_send('standard', obj3)
    r2 = await communicator.receive_json_from()
    assert r1 == r2 == {'detail': 'Update notification'}

    await communicator.disconnect()


async def test_subscribe_permissions_no_user_without_auth_middleware(db_obj):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'permissions/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(bytes_data=to_json({'payload': {'counter': 0}}))
    response = await communicator.receive_json_from()
    expected = {'status': 403, 'text': {
        'detail': 'You do not have permission to perform this action.',
    }}
    assert response == expected

    await api_send('permissions', db_obj)
    await asyncio.sleep(0.1)
    assert communicator.output_queue.empty()

    await communicator.disconnect()


async def test_subscribe_perms_with_admin_no_auth_middleware(db_obj, admin):
    # No permission because of missing middleware
    communicator = await AuthWebsocketCommunicator(
        APITestDemultiplexer,
        'permissions/subscribe/',
        user=admin,
    )
    connected, subprotocol = await communicator.connect()
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    expected = {'status': 403, 'text': {
        'detail': 'You do not have permission to perform this action.',
    }}
    assert response == expected

    await api_send('permissions', db_obj)
    await asyncio.sleep(0.1)
    assert communicator.output_queue.empty()

    await communicator.disconnect()


async def test_subscribe_permissions_no_user_with_auth_middleware(db_obj):
    communicator = await AuthWebsocketCommunicator(
        AuthMiddlewareStack(APITestDemultiplexer),
        'permissions/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    expected = {'status': 403, 'text': {
        'detail': 'You do not have permission to perform this action.',
    }}
    assert response == expected

    await api_send('permissions', db_obj)
    await asyncio.sleep(0.1)
    assert communicator.output_queue.empty()

    await communicator.disconnect()


async def test_subscribe_perms_with_user_with_auth_middleware(db_obj, user):
    communicator = await AuthWebsocketCommunicator(
        AuthMiddlewareStack(APITestDemultiplexer),
        'permissions/subscribe/',
        user=user,
    )
    connected, subprotocol = await communicator.connect()
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    expected = {'status': 403, 'text': {
        'detail': 'You do not have permission to perform this action.',
    }}
    assert response == expected

    await api_send('permissions', db_obj)
    await asyncio.sleep(0.1)
    assert communicator.output_queue.empty()

    await communicator.disconnect()


async def test_subscribe_perms_with_admin_with_auth_middleware(db_obj, admin):
    communicator = await AuthWebsocketCommunicator(
        AuthMiddlewareStack(APITestDemultiplexer),
        'permissions/subscribe/',
        user=admin,
    )
    connected, subprotocol = await communicator.connect()
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    expected = {'status': 200, 'text': {'detail': 'subscription successful'}}
    assert response == expected

    await api_send('permissions', db_obj)
    response = await communicator.receive_json_from()
    assert response == {'detail': 'Update notification'}

    await communicator.disconnect()


async def test_subscribe_prevent_add_groups_repeatedly(db_obj):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'standard/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    consumer = communicator.instance._consumers['standard']

    assert consumer.groups == set()
    group_name = 'standard-{}'.format(db_obj.pk)
    consumer.groups = {group_name}
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    assert response['status'] == 200
    assert consumer.groups == {group_name}

    await api_send('standard', db_obj)
    await asyncio.sleep(0.1)
    # The channel wasn't added to the group because it was in consumer.groups
    # already
    assert communicator.output_queue.empty()

    await communicator.disconnect()


async def test_subscribe_multiple_groups(db_obj):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'standard/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    consumer = communicator.instance._consumers['standard']

    consumer.groups = {'some-group', 'standard2'}
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    assert response['status'] == 200
    expected = {'standard-{}'.format(db_obj.pk), 'some-group', 'standard2'}
    assert consumer.groups == expected

    await api_send('standard', db_obj)
    response = await communicator.receive_json_from()
    assert response == {'detail': 'Update notification'}

    await communicator.disconnect()


async def test_subscribe_with_mapping(db_obj):
    """
    The mapping is defined in the consumer:
    names -> name__in
    counter_max -> counter__lte
    """
    sara = await create_obj(name='Sara', counter=4)
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'mapping/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(bytes_data=to_json({
        'payload': {
            'names': ['Mike', 'Sara'],
            'counter_max': 5,
        },
    }))
    response = await communicator.receive_json_from()
    expected = {'status': 200, 'text': {'detail': 'subscription successful'}}
    assert response == expected
    groups = communicator.instance._consumers['mapping'].groups
    assert not {'mapping-{}'.format(obj.pk) for obj in (db_obj, sara)} - groups

    await api_send('mapping', db_obj)
    r1 = await communicator.receive_json_from()
    await api_send('mapping', sara)
    r2 = await communicator.receive_json_from()
    assert r1 == r2 == {'detail': 'Update notification'}

    await communicator.disconnect()


# unsubscribe

async def test_unsubscribe(db_obj):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'standard/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    groups = communicator.instance._consumers['standard'].groups
    assert groups == set()
    groups.add('-test-')

    # Subscribe first
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_from()
    assert groups == {'standard-{}'.format(db_obj.pk), '-test-'}

    # Unsubscribe
    communicator.scope['path'] = 'standard/unsubscribe/'
    # await communicator.send_json_to({'id': db_obj.pk})
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    expected = {'status': 204, 'text': {'detail': 'subscription cancelled'}}
    assert response == expected
    assert groups == {'-test-'}

    await communicator.disconnect()


async def test_unsubscribe_without_prior_subscription(db_obj):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'standard/unsubscribe/',
    )
    await communicator.send_to(
        bytes_data=to_json({'payload': {'id': db_obj.pk}})
    )
    response = await communicator.receive_json_from()
    expected = {'status': 204, 'text': {'detail': 'subscription cancelled'}}
    assert response == expected
    await communicator.disconnect()


# Creation
# --------

@pytest.mark.django_db
async def test_create(subscribed_communicator):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/create/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(bytes_data=to_json({
        'payload': {
            'name': 'Richard',
            'counter': 500,
        },
    }))
    response = await communicator.receive_json_from()
    expected = {'status': 201, 'text': {'detail': 'creation successful'}}
    assert response == expected
    await communicator.disconnect()

    response = await subscribed_communicator.receive_json_from()
    assert response['action'] == 'create'
    assert isinstance(response['data']['id'], int)
    assert response['data']['name'] == 'Richard'
    assert response['data']['counter'] == 500
    await subscribed_communicator.disconnect()

    count = await database_sync_to_async(
        models.APIModel.objects.filter(name='Richard', counter=500).count
    )()
    assert count == 1


# Updates
# -------

async def test_update(db_obj, subscribed_communicator):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/update/{}/'.format(db_obj.pk),
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(bytes_data=to_json({
        'payload': {
            'counter': 21,
        },
    }))
    response = await communicator.receive_json_from()
    assert response == {'status': 200, 'text': {'detail': 'update successful'}}

    response = await subscribed_communicator.receive_json_from()
    expected = {
        'action': 'update',
        'data': {'id': db_obj.pk, 'name': 'Mike', 'counter': 21},
    }
    assert response == expected
    await subscribed_communicator.disconnect()

    await communicator.disconnect()
    await database_sync_to_async(db_obj.refresh_from_db)()
    assert db_obj.counter == 21


# Deletion
# --------

async def test_delete(db_obj, subscribed_communicator):
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/delete/{}/'.format(db_obj.pk),
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(bytes_data=to_json({}))
    response = await communicator.receive_json_from()
    expected = {'status': 204, 'text': {'detail': 'deletion successful'}}
    assert response == expected
    await communicator.disconnect()

    response = await subscribed_communicator.receive_json_from()
    expected = {
        'action': 'delete',
        'data': {'id': db_obj.pk},
    }
    assert response == expected
    await subscribed_communicator.disconnect()

    with pytest.raises(ObjectDoesNotExist):
        await database_sync_to_async(db_obj.refresh_from_db)()


# Responses
# ---------

# send

async def test_send():
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    consumer = communicator.instance._consumers['test-obj']
    await sync_to_async(consumer.send)(test=True, message='This is a test')
    response = await communicator.receive_json_from()
    assert response == {'test': True, 'message': 'This is a test'}


# group_send

async def test_group_send(subscribed_communicator):
    consumer = subscribed_communicator.instance._consumers['test-obj']
    await sync_to_async(consumer.group_send)('test-obj', 'Hello!')
    response = await subscribed_communicator.receive_json_from()
    assert response == {'action': 'subscribe', 'data': 'Hello!'}


# handle_exception

async def test_handle_exception():
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/subscribe/',
    )
    connected, subprotocol = await communicator.connect()
    consumer = communicator.instance._consumers['test-obj']

    # APIException
    await sync_to_async(consumer.handle_exception)(
        APIException('Something went wrong.')
    )
    response = await communicator.receive_json_from()
    expected = {'status': 500, 'text': {'detail': 'Something went wrong.'}}
    assert response == expected

    # Another exception
    class TestException(Exception):
        pass

    with pytest.raises(TestException):
        try:
            raise TestException
        except TestException as e:
            consumer.handle_exception(e)


# APIDemultiplexer
# ================

# receive

async def test_receive():
    # TODO Test url
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        '/',
    )
    connected, subprotocol = await communicator.connect()
    assert connected
    await communicator.send_to(bytes_data=to_json({'payload': {'id': 100}}))
    response = await communicator.receive_json_from()
    assert response == {'status': 404, 'detail': 'Not found'}

    await communicator.disconnect()


# api_send

async def test_api_send():
    class TestConsumer(APIDemultiplexer):
        groups = ['group']

        async def receive(self, text_data=None, bytes_data=None):
            if text_data == 'direct':
                await self.api_send({'text': 'response'})
            else:
                await self.channel_layer.group_send(
                    'group',
                    {'type': 'api.send', 'text': 'via group'},
                )

    communicator = WebsocketCommunicator(TestConsumer, '/')
    # Via method call
    await communicator.connect()
    await communicator.send_to('direct')
    response = await communicator.receive_from()
    assert response == 'response'

    # Via group and type call from within consumer
    await communicator.send_to('I want the group')
    response = await communicator.receive_from()
    assert response == 'via group'

    # Via group and type call from outside
    await channel_layer.group_send(
        'group',
        {'type': 'api.send', 'text': 'Anybody there?'},
    )
    response = await communicator.receive_from()
    assert response == 'Anybody there?'

    await communicator.disconnect()


# websocket_disconnect
async def test_websocket_disconnet():
    communicator = WebsocketCommunicator(
        APITestDemultiplexer,
        'test-obj/subscribe/'
    )
    connected, subprotocol = await communicator.connect()
    communicator.instance._consumers['test-obj'].groups.add('a-group')
    assert communicator.instance.groups == ['.user']
    with pytest.raises(StopConsumer):
        await communicator.instance.websocket_disconnect({'code': -1})
    assert communicator.instance.groups == ['.user', 'a-group']

    await communicator.disconnect()
