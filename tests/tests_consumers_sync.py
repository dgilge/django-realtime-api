import json
import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.views import View
from djangorestframework_camel_case.parser import CamelCaseJSONParser
from djangorestframework_camel_case.render import CamelCaseJSONRenderer
from rest_framework import (
    authentication as rest_framework_auth, exceptions, parsers, renderers
)
from rest_framework.serializers import ModelSerializer
from rest_framework.settings import api_settings

from realtime_api.consumers import (
    APIConsumer, SelfContainedAPIConsumer, AlreadyRegistered, APIDemultiplexer
)
from realtime_api.authentication import SessionAuthentication

from . import consumers, models, serializers, views


def payload(**data):
    return json.dumps({'payload': data}).encode()


def consumer_with_custom_serializer_meta(**kwargs):
    kwargs.update({'model': models.APIModel})
    Meta = type('Meta', (), kwargs)
    Serializer = type('Serializer', (ModelSerializer,), {'Meta': Meta})
    return type('TestConsumer', (consumers.SelfContainedTestConsumer,), {
        'view': views.TestViewSet,
        'serializer_class': Serializer,
        'parser': CamelCaseJSONParser(),
    })()


@pytest.fixture
def db_obj(db):
    return models.APIModel.objects.create(name='Emily', counter=2)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user('test-user', 'pw')


@pytest.fixture
def user_consumer(user):
    return type('UserConsumer', (), {'scope': {'user': user}})


# APIConsumer
# ===========

# Signals
# -------

# register

def test_register_view_is_none_in_dict():
    class TestConsumer(APIConsumer):
        view = None

    assert TestConsumer.register() is None


def test_register_view_is_none_not_in_dict():
    class TestConsumer(APIConsumer):
        pass

    with pytest.raises(ValueError) as e:
        TestConsumer.register()
    msg = "'TestConsumer' should include a 'view' attribute."
    assert str(e.value) == msg


# Initialization
# --------------

# get_model

def test_get_model():
    class TestConsumer(SelfContainedAPIConsumer):
        view = type('TempView', (views.TestViewSet,), {})

    # From queryset
    assert TestConsumer.get_model() is models.APIModel

    # Not defined
    TestConsumer.model = None
    TestConsumer.view.queryset = None
    with pytest.raises(ImproperlyConfigured) as e:
        TestConsumer.get_model()
    msg = (
        "Either 'TestConsumer' should include a 'model' attribute "
        "or 'TempView' a 'queryset' attribute."
    )
    assert str(e.value) == msg

    # View not defined
    TestConsumer.view = None
    with pytest.raises(ValueError) as e:
        TestConsumer.get_model()
    msg = "'TestConsumer' should include a 'view' attribute."
    assert str(e.value) == msg

    # From model
    TestConsumer.model = models.ProxyModel
    assert TestConsumer.get_model() is models.ProxyModel


# __init__

def test_view_not_apiview():
    class TestConsumer(APIConsumer):
        view = type(
            'QuerysetView',
            (View,),
            {'queryset': models.APIModel.objects.all()},
        )

    with pytest.raises(AssertionError) as e:
        TestConsumer()
    msg = (
        "'The 'view' attribute of 'TestConsumer' should be a subclass of "
        "'APIView.'"
    )
    assert str(e.value) == msg


def test_constructor_without_consumer():
    consumer = consumers.TestConsumer()
    assert isinstance(consumer.view, views.TestViewSet)
    assert consumer.user is None
    assert consumer.view.request.user is None
    assert issubclass(consumer.serializer_class, serializers.TestSerializer)
    assert isinstance(consumer.renderer, CamelCaseJSONRenderer)
    assert not hasattr(consumer, 'consumer')
    assert consumer.parser is None


def test_constructor_with_consumer_without_user():
    consumer = consumers.TestConsumer(consumers.DummyConsumer())
    assert isinstance(consumer.view, views.TestViewSet)
    assert consumer.user is None
    assert consumer.view.request.user is None
    assert issubclass(consumer.serializer_class, serializers.TestSerializer)
    assert isinstance(consumer.renderer, CamelCaseJSONRenderer)
    assert isinstance(consumer.consumer, consumers.DummyConsumer)
    assert isinstance(consumer.parser, CamelCaseJSONParser)


def test_constructor_with_consumer_with_user(user_consumer):
    user = user_consumer.scope['user']
    consumer = consumers.TestConsumer(user_consumer)
    assert consumer.user is user
    assert consumer.view.request.user is user


# discover_perform_methods

def test_ignore_view_nothing_overridden():
    consumer = consumers.TestConsumer(consumers.DummyConsumer())
    ignore = {'perform_create', 'perform_update', 'perform_destroy'}
    assert consumer._ignore_view == ignore


def test_ignore_view_update_overridden_in_consumer():
    consumer = consumers.PerformUpdateConsumer(consumers.DummyConsumer())
    ignore = {'perform_create', 'perform_update', 'perform_destroy'}
    assert consumer._ignore_view == ignore


def test_ignore_view_all_overridden_parts_in_parents_consumer():
    consumer = consumers.PerformCreateUpdateDeleteConsumer(
        consumers.DummyConsumer()
    )
    ignore = {'perform_create', 'perform_update', 'perform_destroy'}
    assert consumer._ignore_view == ignore


def test_ignore_view_create_update_overridden_in_view():
    class TestConsumer(SelfContainedAPIConsumer):
        view = views.PerformCreateUpdateViewSet
        stream = 'stream'

    consumer = TestConsumer(consumers.DummyConsumer())
    assert consumer._ignore_view == {'perform_destroy'}


def test_ignore_view_all_overridden_parts_in_parents_view():
    class TestConsumer(SelfContainedAPIConsumer):
        view = views.PerformCreateUpdateDeleteViewSet
        stream = 'stream'

    consumer = TestConsumer(consumers.DummyConsumer())
    assert consumer._ignore_view == set()


def test_ignore_view_update_overridden_in_consumer_and_all_in_view():
    class TestConsumer(consumers.PerformUpdateConsumer):
        view = views.PerformCreateUpdateDeleteViewSet

    consumer = TestConsumer(consumers.DummyConsumer())
    assert consumer._ignore_view == {'perform_update'}


# receive

@pytest.mark.django_db
def test_receive_attributes():
    consumer = consumers.TestConsumer(consumers.DummyConsumer())

    # URL
    url = ('path', 'create')
    consumer.receive(url, bytes_data=payload(name='Sander', counter=0))
    assert consumer.url == url
    assert consumer.action == 'create'

    # Data
    consumer.receive(url, 'text')
    assert consumer.data == b'text'
    consumer.receive(url, '', b'bytes')
    assert consumer.data == b'bytes'


def test_receive_action_not_allowed():
    consumer = consumers.ExceptionToMessageConsumer(consumers.DummyConsumer())
    consumer.receive(('path', 'not-allowed'), text_data='')
    error = consumer.messages.pop()
    assert isinstance(error, exceptions.MethodNotAllowed)
    assert error.detail == 'Action "not-allowed" not allowed.'


def test_receive_exception(db_obj):
    class TestConsumer(consumers.ExceptionToMessageConsumer):
        def perform_update(self, serializer):
            assert isinstance(self.object, models.APIModel)
            raise exceptions.APIException('Just an error')

    consumer = TestConsumer()
    consumer.parser = CamelCaseJSONParser()
    assert consumer.object is None
    consumer.receive(
        ('go', 'update', str(db_obj.pk)),
        json.dumps({'payload': {'counter': 301}}),
    )
    error = consumer.messages.pop()
    assert isinstance(error, exceptions.APIException)
    assert error.detail == 'Just an error'
    assert consumer.object is None


def test_object_concurrency():
    pass  # TODO


# action_allowed

def test_action_allowed():
    consumer = consumers.TestConsumer()
    consumer.action = 'x'

    # Always allowed
    assert consumer.action_allowed('subscribe')
    assert consumer.action_allowed('unsubscribe')

    # From view
    assert consumer.allowed_actions is None
    assert consumer.action_allowed('create')
    assert consumer.action_allowed('update')
    assert consumer.action_allowed('delete')
    assert not consumer.action_allowed('not-allowed')

    # From allowed_actions
    consumer.allowed_actions = ('custom',)
    assert not consumer.action_allowed('create')
    assert not consumer.action_allowed('update')
    assert not consumer.action_allowed('delete')
    assert consumer.action_allowed('custom')


# get_serializer_class

def test_get_serializer_class_correct_order():
    consumer = consumers.TestConsumer()
    consumer.immediate_broadcast = False

    # Serializer from view.get_serializer
    serializer = consumer.get_serializer_class()()
    assert isinstance(serializer, serializers.TestSerializer)

    # Serializer from serializer_class
    del consumer._cached_serializer_class
    consumer.serializer_class = 1
    serializer = consumer.get_serializer_class()
    assert serializer == 1

    # No serializer to find
    del consumer._cached_serializer_class
    consumer.serializer_class = None
    consumer.view = type('FakeView', (), {})()
    with pytest.raises(ImproperlyConfigured) as e:
        consumer.get_serializer_class()
    msg = (
        "Either 'TestConsumer' or 'FakeView' should include a "
        "'serializer_class' attribute."
    )
    assert str(e.value) == msg


def test_get_serializer_class_immediate_broadcast():
    consumer = consumers.TestConsumer()
    serializer = consumer.get_serializer_class()
    assert serializer.__name__ == 'ImmediateBroadcastTestSerializer'
    assert issubclass(serializer, serializers.TestSerializer)
    assert serializer.__module__ == 'realtime_api.dynamic'
    # todo Test that model isn't reregistered

    # Serializer's Meta
    assert issubclass(
        serializer.Meta.model,
        serializers.TestSerializer.Meta.model
    )
    # Attributes of serializer's parents are applied
    assert serializer.Meta.fields == serializers.TestSerializer.Meta.fields

    # Serializer's model
    model = serializer.Meta.model
    assert model.__name__ == 'ImmediateBroadcastAPIModel'
    assert issubclass(model, models.APIModel)
    assert model.__module__ == 'realtime_api.dynamic'
    assert model.signal_broadcast is False

    # Model's Meta
    assert model._meta.proxy is True
    # Attributes of model's parents are applied
    assert model._meta.get_latest_by == 'id'

    # Serializer from serializer_class
    consumer.serializer_class = serializers.ProxySerializer
    del consumer._cached_serializer_class
    serializer = consumer.get_serializer_class()
    assert serializer.__name__ == 'ImmediateBroadcastProxySerializer'
    assert issubclass(serializer, serializers.ProxySerializer)


def test_field_names():
    consumer = consumer_with_custom_serializer_meta(fields=['counter'])
    assert consumer.field_names == ['counter']

    consumer = consumer_with_custom_serializer_meta(fields='__all__')
    assert consumer.field_names == ['id', 'name', 'counter']

    consumer = consumer_with_custom_serializer_meta(exclude=['counter'])
    assert consumer.field_names == ['id', 'name']

    with pytest.raises(AssertionError):
        consumer = consumer_with_custom_serializer_meta()

    with pytest.raises(AssertionError):
        consumer = consumer_with_custom_serializer_meta(
            fields=['name'],
            exclude=['counter'],
        )


# get_json_version

def test_get_json_version():
    """
    Tests for renderer and parser.
    """
    consumer = consumers.TestConsumer()
    renderer = consumer.get_json_version('get_renderers')
    assert isinstance(renderer, CamelCaseJSONRenderer)
    parser = consumer.get_json_version('get_parsers')
    assert isinstance(parser, CamelCaseJSONParser)


def test_get_json_version_none_found():
    """
    Tests for renderer and parser.
    """
    consumer = consumers.TestConsumer()
    consumer.view.renderer_classes = (
        renderers.TemplateHTMLRenderer,
        renderers.MultiPartRenderer,
    )
    consumer.view.parser_classes = (
        parsers.FormParser,
        parsers.MultiPartParser,
    )
    with pytest.raises(ImproperlyConfigured) as e:
        consumer.get_json_version('get_renderers')
    msg = (
        "Either 'TestViewSet' or the 'REST_FRAMEWORK' settings should "
        "include a JSON version for the renderers."
    )
    assert str(e.value) == msg

    with pytest.raises(ImproperlyConfigured) as e:
        consumer.get_json_version('get_parsers')
    msg = (
        "Either 'TestViewSet' or the 'REST_FRAMEWORK' settings should "
        "include a JSON version for the parsers."
    )
    assert str(e.value) == msg


# get_object

def test_get_object_update_delete(db_obj):
    consumer = consumers.TestConsumer()
    consumer.url = ('path', 'action', str(db_obj.pk))
    assert consumer.get_object() == consumer.object == db_obj


def test_get_object_create(db_obj):
    consumer = consumers.TestConsumer()
    consumer.action = 'create'
    assert consumer.get_object() is None


def test_get_object_cached(db_obj):
    consumer = consumers.TestConsumer()
    consumer.object = 0
    assert consumer.get_object() == 0


def test_get_object_custom_lookup_field(db_obj):
    consumer = consumers.TestConsumer()
    consumer.lookup_field = 'name__iexact'
    consumer.url = ('path', 'action', 'emily')
    assert consumer.get_object() == consumer.object == db_obj


def test_get_object_lookup_value_missing():
    consumer = consumers.TestConsumer()
    with pytest.raises(exceptions.NotFound) as e:
        consumer.get_object()
    msg = 'The URL should include a lookup value.'
    assert str(e.value) == msg


@pytest.mark.django_db
def test_get_object_not_found():
    consumer = consumers.TestConsumer()
    consumer.url = ('path', 'action', 2873462)
    with pytest.raises(exceptions.NotFound):
        consumer.get_object()


@pytest.mark.django_db
def test_get_object_invalid_lookup_value():
    consumer = consumers.TestConsumer()
    consumer.url = ('path', 'action', 'invalid')
    with pytest.raises(exceptions.ValidationError) as e:
        consumer.get_object()
    msg = "['The URL lookup value is invalid.']"
    assert str(e.value) == msg


# get_objects

@pytest.mark.django_db
def test_get_objects_fields():
    consumer = consumers.TestConsumer()
    consumer.parser = CamelCaseJSONParser()

    # One field
    consumer.data = payload(counter=41)

    # Empty database
    assert not consumer.get_objects().exists()

    models.APIModel.objects.bulk_create((
        models.APIModel(name='Luke', counter=41),
        models.APIModel(name='Mary', counter=41),
        models.APIModel(name='Luke', counter=26),
    ))

    # 2 objects
    objects = list(consumer.get_objects())
    assert len(objects) == 2
    assert objects[0].counter == objects[1].counter == 41

    # 1 object
    consumer.data = payload(counter=26)
    objects = list(consumer.get_objects())
    assert len(objects) == 1
    assert objects[0].name == 'Luke'

    # Two fields
    # 1 object
    consumer.data = payload(name='Luke', counter=41)
    objects = list(consumer.get_objects())
    assert len(objects) == 1
    assert objects[0].name == 'Luke'

    # 0 objects
    consumer.data = payload(name='Mary', counter=26)
    assert not consumer.get_objects().exists()


@pytest.fixture
def objects(db):
    # Roll backs after each test don't work
    if models.APIModel.objects.filter(name='Ann').count() < 2:
        models.APIModel.objects.bulk_create((
            models.APIModel(name='Ann', counter=5),
            models.APIModel(name='Mary', counter=5),
            models.APIModel(name='Ann', counter=10),
            models.APIModel(name='ann', counter=10),
        ))


# todo: Maybe some of these tests could be deleted

def test_get_objects_fields_empty(objects):
    consumer = consumer_with_custom_serializer_meta(fields=[])
    consumer.data = payload(name='Ann')
    assert not consumer.get_objects().exists()


def test_get_objects_fields__all__(objects):
    consumer = consumer_with_custom_serializer_meta(fields='__all__')
    consumer.data = payload(name='Luke')
    assert not consumer.get_objects().exists()

    consumer.data = payload(counter=5)
    assert consumer.get_objects().count() == 2

    consumer.data = payload(name='Ann', counter=5)
    assert consumer.get_objects().count() == 1


def test_get_objects_exclude(objects):
    consumer = consumer_with_custom_serializer_meta(exclude=['counter'])
    consumer.data = payload(name='Tom')
    assert not consumer.get_objects().exists()

    consumer.data = payload(counter=5)
    assert not consumer.get_objects().exists()

    consumer.data = payload(name='Ann', counter=5)
    assert consumer.get_objects().count() == 2


def test_get_objects_exclude_empty(objects):
    consumer = consumer_with_custom_serializer_meta(exclude=[])

    consumer.data = payload(name='Luke')
    assert not consumer.get_objects().exists()

    consumer.data = payload(name='Ann')
    assert consumer.get_objects().count() == 2


def test_get_objects_fields_and_exclude_empty(objects):
    consumer = consumer_with_custom_serializer_meta(fields=[], exclude=[])
    consumer.data = payload(name='Ann')
    assert not consumer.get_objects().exists()


def test_get_objects_fields_with_mapping(objects):
    consumer = consumer_with_custom_serializer_meta(fields=['name', 'counter'])
    consumer.subscription_field_mapping = {
        'name': 'name__iexact',
        'names': 'name__in',
        'counter_max': 'counter__lte',
    }
    consumer.data = payload(name=None)
    assert not consumer.get_objects().exists()

    consumer.data = payload(counter=10)
    assert consumer.get_objects().count() == 2

    consumer.data = payload(name='Ann', counter_max=10)
    assert consumer.get_objects().count() == 3

    consumer.data = payload(names=['Ann', 'Sebastian', 'Mary'], counter=10)
    assert consumer.get_objects().count() == 1


def test_get_objects_fields__all__with_mapping(objects):
    consumer = consumer_with_custom_serializer_meta(fields='__all__')
    consumer.subscription_field_mapping = {
        'name__endswith': 'name__endswith',
    }
    consumer.data = payload(names=['ann'])
    assert not consumer.get_objects().exists()

    consumer.data = payload(counter=10)
    assert consumer.get_objects().count() == 2

    consumer.data = payload(name__endswith='nn', counter=10)
    assert consumer.get_objects().count() == 2


def test_get_objects_exclude_with_mapping(objects):
    consumer = consumer_with_custom_serializer_meta(exclude=['counter'])
    consumer.subscription_field_mapping = {
        'search': 'name__istartswith',
        'min': 'counter__gte',
    }
    consumer.data = payload(counter=10)
    assert not consumer.get_objects().exists()

    consumer.data = payload(min=0)
    assert consumer.get_objects().count() >= 4

    consumer.data = payload(search='ann', min=10)
    assert consumer.get_objects().count() == 2


def test_get_objects_override_exclude(objects):
    consumer = consumer_with_custom_serializer_meta(exclude=['counter'])
    consumer.subscription_field_mapping = {'counter': 'counter'}

    consumer.data = payload(counter=10)
    assert consumer.get_objects().count() == 2


def test_get_objects_not_allowed_field(objects):
    consumer = consumer_with_custom_serializer_meta(fields='__all__')

    consumer.data = payload(non_field=10, doesnotexist='no')
    assert not consumer.get_objects().exists()

    consumer.data = payload(__all__='__all__')
    assert not consumer.get_objects().exists()

    consumer.data = payload(counter=5, non_field=10, doesnotexist='no')
    assert consumer.get_objects().count() == 2


def test_get_objects_invalid_lookup_value(objects):
    consumer = consumer_with_custom_serializer_meta(fields=['name', 'counter'])

    consumer.data = payload(name=100)
    assert not consumer.get_objects().exists()

    consumer.data = payload(counter='text')
    with pytest.raises(exceptions.ValidationError) as e:
        consumer.get_objects()
    msg = "['The lookup value is invalid.']"
    assert str(e.value) == msg


# get_queryset

def test_get_queryset(objects):
    class TestConsumer(consumers.SelfContainedTestConsumer):
        view = type('TestView', (views.TestViewSet,), {
            'queryset': models.APIModel.objects.filter(counter=10),
        })

    consumer = TestConsumer()
    objects = list(consumer.get_queryset())
    assert len(objects) == 2
    assert {objects[0].name, objects[1].name} == {'Ann', 'ann'}


# get_group_name

def test_get_group_name():
    obj = type('FakeObj', (), {'pk': 100})
    consumer = consumers.SelfContainedTestConsumer()
    assert consumer.get_group_name(obj) == 'sc-100'

    # No stream
    consumer.stream = ''
    with pytest.raises(AssertionError) as e:
        consumer.get_group_name(obj)
    msg = "'SelfContainedTestConsumer' should include a 'stream' attribute."
    assert str(e.value) == msg


# get_group_names

def test_get_group_names():
    consumer = consumers.SelfContainedTestConsumer()
    names = consumer.get_group_names([
        type('FakeModel', (), {'pk': 1})(),
        type('FakeModel', (), {'pk': 'de4ckDsk'})(),
    ])
    assert names == {'sc-1', 'sc-de4ckDsk'}


# Creation
# --------

# perform_create

@pytest.mark.django_db
def test_perform_create():
    consumer = consumers.SelfContainedTestConsumer(consumers.DummyConsumer())
    consumer.action = 'create'
    consumer.data = payload(name='Elisabeth', counter=500)
    serializer = consumer.deserialize()
    instance = consumer.perform_create(serializer)
    assert isinstance(instance, models.APIModel)

    consumer._ignore_view = []
    serializer = consumer.deserialize()
    instance = consumer.perform_create(serializer)
    assert instance is None

    queryset = models.APIModel.objects.filter(name='Elisabeth', counter=500)
    assert queryset.count() == 2


# group_send_post_create

def test_group_send_post_create():
    consumer = consumers.SelfContainedTestConsumer()
    with pytest.raises(AssertionError) as e:
        consumer.group_send_post_create(None)
    msg = "'perform_create' should return the saved instance."
    assert str(e.value) == msg


# Updates
# -------

# perform_update

def test_perform_update_from_consumer(db_obj):
    consumer = consumers.SelfContainedTestConsumer(consumers.DummyConsumer())
    consumer.action = 'update'
    consumer.data = payload(name='Sabina', counter=155)
    serializer = consumer.deserialize(db_obj, True)
    instance = consumer.perform_update(serializer)
    assert isinstance(instance, models.APIModel)
    db_obj.refresh_from_db()
    assert db_obj.name == 'Sabina'
    assert db_obj.counter == 155


def test_perform_update_from_view(db_obj):
    consumer = consumers.SelfContainedTestConsumer(consumers.DummyConsumer())
    consumer.action = 'update'
    consumer._ignore_view = []
    consumer.data = payload(name='Sabina', counter=277)
    serializer = consumer.deserialize(db_obj, True)
    instance = consumer.perform_update(serializer)
    assert instance is None
    db_obj.refresh_from_db()
    assert db_obj.name == 'Sabina'
    assert db_obj.counter == 277


# group_send_post_update

def test_group_send_post_update():
    consumer = consumers.SelfContainedTestConsumer(consumers.DummyConsumer())
    with pytest.raises(AssertionError) as e:
        consumer.group_send_post_update(None)
    msg = "'perform_update' should return the saved instance."
    assert str(e.value) == msg


# Deletion
# --------

# perform_destroy

def test_perform_destroy_from_consumer(db_obj):
    consumer = consumers.SelfContainedTestConsumer()
    consumer.perform_destroy(db_obj)
    with pytest.raises(ObjectDoesNotExist):
        db_obj.refresh_from_db()


def test_perform_destroy_from_view(db_obj):
    consumer = consumers.SelfContainedTestConsumer()
    consumer._ignore_view = []
    consumer.perform_destroy(db_obj)
    with pytest.raises(ObjectDoesNotExist):
        db_obj.refresh_from_db()


# Processing
# ----------

# serialize

def test_serialize(db_obj):
    consumer = consumers.SelfContainedTestConsumer()
    data = consumer.serialize(db_obj)
    assert data == {'id': db_obj.pk, 'name': 'Emily', 'counter': 2}

    data = consumer.serialize(None)
    assert data == {'counter': None, 'name': ''}


# deserialize

def test_deserialize_no_obj():
    consumer = consumers.SelfContainedTestConsumer()
    consumer.action = 'subscribe'
    consumer.parser = CamelCaseJSONParser()

    # No data
    consumer.data = payload()
    assert consumer.deserialize(partial=True).validated_data == {}

    # Partial data
    consumer.data = payload(name='Andrew')
    with pytest.raises(exceptions.ValidationError):
        consumer.deserialize()

    data = consumer.deserialize(partial=True).validated_data
    assert data == {'name': 'Andrew'}

    # Complete data
    consumer.data = payload(something=1, name='Andrew', counter=270)
    data = consumer.deserialize().validated_data
    assert data == {'name': 'Andrew', 'counter': 270}


def test_deserialize_with_obj(db_obj):
    consumer = consumers.SelfContainedTestConsumer()
    consumer.action = 'subscribe'
    consumer.parser = CamelCaseJSONParser()

    # No data
    consumer.data = payload()
    assert consumer.deserialize(db_obj, partial=True).validated_data == {}

    # Partial data
    consumer.data = payload(counter=270)
    with pytest.raises(exceptions.ValidationError):
        consumer.deserialize()

    data = consumer.deserialize(db_obj, partial=True).validated_data
    assert data == {'counter': 270}

    # Complete data
    consumer.data = payload(id=234, name='Andrew', counter=270)
    data = consumer.deserialize(db_obj).validated_data
    assert data == {'name': 'Andrew', 'counter': 270}

    consumer.data = payload(id=db_obj.pk, name='Andrew', counter=270)
    data = consumer.deserialize(db_obj).validated_data
    assert data == {'name': 'Andrew', 'counter': 270}


def test_deserialize_permissions():
    class TestConsumer(consumers.SelfContainedTestConsumer):
        view = views.TestViewSet

        def check_permissions(self, obj):
            raise exceptions.APIException()

    consumer = TestConsumer()
    consumer.parser = CamelCaseJSONParser()
    consumer.data = payload(name='me', counter=0)
    with pytest.raises(exceptions.APIException):
        consumer.deserialize()


def test_deserialize_exception():
    consumer = consumers.SelfContainedTestConsumer()
    consumer.action = 'subscribe'
    consumer.parser = CamelCaseJSONParser()
    consumer.data = payload(id=234, city='London', counter=15)
    with pytest.raises(exceptions.APIException):
        consumer.deserialize()

    consumer.data = payload(id=234, name='Natasha', counter='-')
    with pytest.raises(exceptions.APIException):
        consumer.deserialize()


# render

def test_render():
    consumer = consumers.SelfContainedTestConsumer()
    data = consumer.render({'first_name': 'André'})
    assert data == '{"firstName":"André"}'


# parse

def test_parse():
    consumer = consumers.SelfContainedTestConsumer()
    consumer.parser = CamelCaseJSONParser()
    consumer.data = b'{"payload":{"field":"v","anotherF":["Espa\u00F1a",""]}}'
    data = consumer.parse()
    assert data == {'field': 'v', 'another_f': ['España', '']}


# Authentication
# --------------

# perform_authentication

def test_perform_authentication_own_class(user):
    class TestConsumer(consumers.SelfContainedAPIConsumer):
        # The view's authentication class isn't used.
        # If so it raised an error.
        view = type('TestView', (views.TestViewSet,), {
            'authentication_classes': (
                rest_framework_auth.BasicAuthentication,
            )
        })
        authentication_classes = [
            type('FakeAuth', (), {'authenticate': lambda self, user: None}),
            SessionAuthentication,
        ]

    consumer = TestConsumer()

    # No user
    assert consumer.perform_authentication() is None

    # Anonymous
    consumer.user = api_settings.UNAUTHENTICATED_USER
    consumer.perform_authentication()
    assert consumer.perform_authentication() is None

    # Authenticated (a user object has is_authenticated == True)
    consumer = TestConsumer()
    consumer.user = user
    assert consumer.perform_authentication() is None


def test_perform_authentication_from_view_class(user):
    class TestConsumer(consumers.SelfContainedAPIConsumer):
        view = views.SessionAuthenticationViewSet

    consumer = TestConsumer()

    # No user
    assert consumer.perform_authentication() is None

    # Anonymous
    consumer.user = api_settings.UNAUTHENTICATED_USER
    assert consumer.perform_authentication() is None

    # Authenticated
    consumer = TestConsumer()
    consumer.user = user
    assert consumer.perform_authentication() is None


@pytest.mark.xfail
def test_perform_authentication_exception(user):
    class TestConsumer(consumers.SelfContainedAPIConsumer):
        view = type('TestView', (views.TestViewSet,), {
            'authentication_classes': (
                rest_framework_auth.SessionAuthentication,
                rest_framework_auth.BasicAuthentication,
            )
        })

    consumer = TestConsumer()
    with pytest.raises(NotImplementedError) as e:
        consumer.perform_authentication()
    msg = (
        "'TestConsumer' should include an 'authentication_classes' attribute."
    )
    assert str(e.value) == msg


# check_permissions

@pytest.mark.parametrize(
    'action', ('subscribe', 'unsubscribe', 'create', 'update', 'delete')
)
def test_check_permissions(db_obj, user, action):
    class TestConsumer(consumers.SelfContainedAPIConsumer):
        view = views.PermissionViewSet

    consumer = TestConsumer()

    consumer.action = action

    # No user
    consumer.user = None
    with pytest.raises(exceptions.PermissionDenied) as e:
        consumer.check_permissions(db_obj)
    msg = 'You do not have permission to perform this action.'
    assert str(e.value) == msg

    # Anonymous
    consumer.user = api_settings.UNAUTHENTICATED_USER
    with pytest.raises(exceptions.PermissionDenied) as e:
        consumer.check_permissions(db_obj)
    assert str(e.value) == msg

    # Authenticated
    user.is_staff = True
    user.is_superuser = True
    user.save()
    consumer.user = user
    assert consumer.check_permissions(db_obj) is None
    assert consumer.check_permissions(None) is None


def test_check_permissions_object():
    class TestConsumer(consumers.SelfContainedAPIConsumer):
        view = type('TestView', (views.TestViewSet,), {
            'permission_classes': (type('FakePermission', (), {
                'has_permission': lambda self, request, view: True,
                'has_object_permission': lambda s, request, view, obj: False,
            }),),
        })
        action = 'subscribe'

    consumer = TestConsumer()
    with pytest.raises(exceptions.PermissionDenied):
        consumer.check_permissions(None)


def test_check_permissions_exception():
    class TestConsumer(consumers.SelfContainedAPIConsumer):
        view = type('TestView', (views.TestViewSet,), {
            'permission_classes': (type('FakePermission', (), {
                'has_permission': None,
                'has_object_permission': lambda s, request, view, obj: False,
            }),),
        })
        action = 'subscribe'

    consumer = TestConsumer()
    with pytest.raises(NotImplementedError) as e:
        consumer.check_permissions(None)
    msg = (
        "'FakePermission' might need a custom implementation. "
        "You could override 'check_permissions' in 'TestConsumer'. "
        "The original error is:\nTypeError: 'NoneType' object is not callable"
    )
    assert str(e.value) == msg


# APIDemultiplexer
# ================

# register

def test_register():
    class TestDemultiplexer(APIDemultiplexer):
        _registry = {}

    class Consumer1(consumers.APIConsumer):
        view = views.TestViewSet
        stream = 'fake1'

    class Consumer2(consumers.APIConsumer):
        view = views.TestViewSet
        stream = 'fake2'

    TestDemultiplexer.register(Consumer1, Consumer2)
    registry = {'fake1': Consumer1, 'fake2': Consumer2}
    assert TestDemultiplexer._registry == registry

    # Initialization
    demultiplexer = TestDemultiplexer({})
    assert isinstance(demultiplexer._consumers.get('fake1'), Consumer1)
    assert isinstance(demultiplexer._consumers.get('fake2'), Consumer2)


def test_register_exceptions():
    class TestDemultiplexer(APIDemultiplexer):
        _registry = {}

    class ConsumerNone(consumers.APIConsumer):
        view = views.TestViewSet
        stream = None

    class Consumer1(consumers.APIConsumer):
        view = views.TestViewSet
        stream = 'fake'

    class Consumer2(consumers.APIConsumer):
        view = views.TestViewSet
        stream = 'fake'

    with pytest.raises(AssertionError) as e:
        TestDemultiplexer.register(ConsumerNone)
    msg = "'ConsumerNone' should include a 'stream' attribute."
    assert str(e.value) == msg

    with pytest.raises(AlreadyRegistered) as e:
        TestDemultiplexer.register(Consumer1, Consumer2)
    msg = "The name 'fake' is registered for 'Consumer1' already."
    assert str(e.value) == msg
