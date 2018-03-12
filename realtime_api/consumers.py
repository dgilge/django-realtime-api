# Parts of this code are taken from Channels.
# For copyright information see licenses.txt.
# https://github.com/django/channels/blob/
#     7ab21c484630d8ba08ea6d3b6d72bd740d3af5ab/channels/binding/base.py
# https://github.com/django/channels/blob/1.x/channels/binding/base.py

import copy
import io
import json
from asgiref.sync import async_to_sync
from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer
from django.apps import apps
from django.db.models.signals import post_delete, post_save
from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from rest_framework import (
    authentication as rest_framework_auth, exceptions, mixins, status
)
from rest_framework.settings import api_settings
from rest_framework.utils import model_meta
from rest_framework.views import APIView

from realtime_api.utils import get_group_user_key

from .authentication import SessionAuthentication


SUBSCRIBE = 'subscribe'
UNSUBSCRIBE = 'unsubscribe'
CREATE = 'create'
UPDATE = 'update'
DELETE = 'delete'


class SelfContainedAPIConsumer:
    """
    Receives and broadcasts messages with a given view and for a given stream.
    """
    # Design notes:
    # The handling of all actions is in this one class and not available as
    # mixins because you should be able to just set the view attribute
    # which will decide which actions are allowed.

    # You should override
    view = None
    stream = None

    # Override if `view` has no `queryset` attribute
    model = None

    # You may override
    allowed_actions = None  # possible values: ('create', 'update', 'delete')
    lookup_field = 'pk'  # for instance: 'name__iexact'
    immediate_broadcast = True
    serializer_class = None
    subscription_field_mapping = {}
    groups = set()
    _action_mapping = {
        SUBSCRIBE: ['get'],
        UNSUBSCRIBE: ['get'],
        CREATE: ['post'],
        UPDATE: ['put', 'patch'],
        DELETE: ['delete'],
    }

    # TODO not implemented
    authentication_classes = []
    parents_query_lookups = []

    # You should not override (values change dynamically)
    action = None
    url = []
    object = None
    data = None
    renderer = None
    parser = None
    _ignore_view = []

    # Initialization

    @classmethod
    def get_model(cls):
        """
        Returns `model` or `view.queryset.model`.
        """
        try:
            return cls.model or cls.view.queryset.model
        except AttributeError:
            if cls.view is None:
                raise ValueError(
                    "'{consumer_class}' should include a 'view' "
                    "attribute.".format(consumer_class=cls.__name__)
                )
            raise ImproperlyConfigured(
                "Either '{consumer_class}' should include a 'model' attribute "
                "or '{view_class}' a 'queryset' attribute.".format(
                    consumer_class=cls.__name__,
                    view_class=cls.view.__name__,
                )
            )

    def __init__(self, consumer=None, immediate_broadcast=True):
        """
        Sets class attributes and authenticate user.
        """
        if isinstance(self.view, type):
            self.view = self.view()

        assert isinstance(self.view, APIView), (
            "'The 'view' attribute of '{}' should be a subclass of "
            "'APIView.'".format(self.__class__.__name__)
        )

        self.immediate_broadcast = immediate_broadcast
        self.user = consumer.scope.get('user') if consumer else None
        # This is required for view.get_serializer_context:
        self.view.format_kwarg = 'json'
        self.view.request = self.get_request(user=self.user)
        self.serializer_class = self.get_serializer_class()
        self.renderer = self.get_json_version('get_renderers')

        if consumer is None:
            # Initialized via signal
            return

        # Don't have class level variables
        self.groups = self.groups or set()
        self.subscription_field_mapping = self.subscription_field_mapping or {}

        self.consumer = consumer
        self.parser = self.get_json_version('get_parsers')
        # TODO set this only if self.immediate_broadcast == True? -> docs
        self._ignore_view = self.discover_perform_methods()

        try:
            self.perform_authentication()
        except Exception as e:
            self.handle_exception(e)

    def discover_perform_methods(self):
        """
        Determines which methods to use to perform create, update and delete.

        This is necessary for immediate broadcast because DRF doesn't return
        the saved instance the consumer needs to broadcast it.
        """
        # Methods and their base definition classes
        check_methods = {
            'perform_create': mixins.CreateModelMixin,
            'perform_update': mixins.UpdateModelMixin,
            'perform_destroy': mixins.DestroyModelMixin,
        }

        def is_overridden(method, origin, cls=self.__class__):
            original_method = getattr(origin, method)
            instance_method = getattr(cls, method, original_method)
            return instance_method is not original_method

        use_consumer_methods = set()

        # Check if method is overriden in the consumer
        # Use the method in this case
        for method in check_methods:
            if is_overridden(method, SelfContainedAPIConsumer):
                use_consumer_methods.add(method)

        standard_view_methods = set()

        # Check if method is overriden in the view
        # Use the consumer's method if not overridden
        view_class = self.view.__class__
        for method in set(check_methods) - use_consumer_methods:
            if not is_overridden(method, check_methods[method], view_class):
                standard_view_methods.add(method)

        use_consumer_methods.update(standard_view_methods)
        return use_consumer_methods

    def get_serializer_class(self):
        """
        Returns the serializer or a subclass from `self` or the `view`.

        Applied order to find the serializer:

        1. self.serializer_class
        2. self.view.get_serializer
        3. self.view.serializer_class

        Returns a subclass with a subclassed model in order to prevent
        overhead by broadcasting immediately and via binding if
        `immediate_broadcast` is true.
        """
        try:
            return self._cached_serializer_class
        except AttributeError:
            pass

        try:
            serializer_class = self.serializer_class or getattr(
                self.view,
                'get_serializer',
                getattr(self.view, 'serializer_class'),
            )
        except AttributeError:
            raise ImproperlyConfigured(
                "Either '{consumer}' or '{view}' should include a "
                "'serializer_class' attribute.".format(
                    consumer=self.__class__.__name__,
                    view=self.view.__class__.__name__,
                )
            )

        if self.immediate_broadcast:
            serializer = serializer_class()

            # Get serializer field names
            model = getattr(serializer.Meta, 'model')
            info = model_meta.get_field_info(model)
            # todo: Is the copy necessary?
            fields = copy.deepcopy(serializer._declared_fields)
            self.field_names = serializer.get_field_names(fields, info)

            # Get original serializer class
            if not isinstance(serializer_class, type):
                # `get_serializer` returns an instance
                # The class (and not a function) is required for subclassing
                serializer_class = serializer.__class__

            # Get model
            class_prefix = 'ImmediateBroadcast{}'
            model_name = class_prefix.format(model.__name__)
            try:
                # Don't reregister a model
                broadcast_model = apps.get_model('realtime_api', model_name)
            except LookupError:
                # Create proxy model
                broadcast_model = type(model_name, (model,), {
                    'signal_broadcast': False,
                    '__module__': 'realtime_api.dynamic',
                    'Meta': type('Meta', (), {'proxy': True}),
                })

            # Create serializer class
            serializer_class = type(
                class_prefix.format(serializer_class.__name__),
                (serializer_class,),
                {
                    'Meta': type('Meta', (serializer_class.Meta,), {
                        'model': broadcast_model,
                    }),
                    '__module__': 'realtime_api.dynamic',
                }
            )

        # Cache serializer to prevent repeated subclassing
        self._cached_serializer_class = serializer_class
        return serializer_class

    def get_json_version(self, method_name):
        """
        Returns a renderer, parser, etc. which processes JSON.
        """
        for obj in getattr(self.view, method_name)():
            if obj.media_type == 'application/json':
                return obj

        raise ImproperlyConfigured(
            "Either '{cls}' or the 'REST_FRAMEWORK' settings should include "
            "a JSON version for the {name}.".format(
                name=method_name.split('_', 1)[1],
                cls=self.view.__class__.__name__,
            )
        )

    def get_user(self):
        user = self.consumer.scope.get(
            'user',
            api_settings.UNAUTHENTICATED_USER,
        )
        return user

    def get_request(self, **kwargs):
        return type('DummyRequest', (), kwargs)()

    # Handling

    def receive(self, url, text_data=None, bytes_data=None):
        """
        Entry point for incomming requests.
        """
        self.url = url
        self.action = url[1]
        self.data = bytes_data or text_data.encode()

        try:
            # Validate action
            if not self.action_allowed(self.action):
                msg = 'Action "{action}" not allowed.'
                raise exceptions.MethodNotAllowed(
                    self.action,
                    msg.format(action=self.action),
                )

            # Dispatch to the correct method
            getattr(self, self.action)()
        except Exception as e:
            self.handle_exception(e)
        finally:
            # TODO is this safe? Or could multiple requests be handled in one
            # instance at the same time?
            # Clean-up
            self.object = None

    def action_allowed(self, action):
        """
        Checks if `action` is allowed according to own or the view's settings.
        """
        if action in (SUBSCRIBE, UNSUBSCRIBE):
            return True
        if self.allowed_actions:
            return action in self.allowed_actions
        try:
            self._action_mapping[action][0]
        except KeyError:
            return False
        if action == DELETE:
            action = 'destroy'
        return hasattr(self.view, action)

    def get_object(self):
        """
        Returns and sets the current object.
        """
        # TODO Is this safe to do?
        # What happens if the data changes?
        # Get it with select_for_update?
        if self.object is not None:
            return self.object
        if self.action == CREATE:
            return None
        try:
            lookup_value = self.url[2]
        except IndexError:
            raise exceptions.NotFound(
                'The URL should include a lookup value.'
            )
        queryset = self.get_queryset()
        try:
            obj = queryset.get(**{self.lookup_field: lookup_value})
        except ObjectDoesNotExist:
            raise exceptions.NotFound()
        except ValueError:
            raise exceptions.ValidationError(
                'The URL lookup value is invalid.'
            )

        self.object = obj
        return obj

    def get_objects(self):
        """
        Returns the objects for subscription.
        """
        data = self.parse()
        filter_dict = None

        filter_dict = {
            self.subscription_field_mapping.get(k, k): v
            for k, v in data.items()
            if k in self.field_names or k in self.subscription_field_mapping
        }
        queryset = self.get_queryset()
        if not filter_dict:
            return queryset.none()
        try:
            return queryset.filter(**filter_dict)
        except ValueError:
            raise exceptions.ValidationError('The lookup value is invalid.')

    def get_queryset(self):
        """
        Returns the result of `view.get_queryset`.
        """
        return self.view.get_queryset()

    def get_group_name(self, obj):
        """
        Returns the group name for the channel layer.
        """
        assert self.stream, (
            "'{cls}' should include a 'stream' attribute.".format(
                cls=self.__class__.__name__,
            )
        )
        return '{}-{}'.format(self.stream, obj.pk)

    def get_group_names(self, objects, subscribe=False):
        """
        Generates the group names for subscription of all requested objects.
        """
        return {self.get_group_name(obj) for obj in objects}

        # yield ':'.join(
        #     (*obj.__module__.split('.'), obj.__class__.__name__, obj.pk)
        # )

    # Subscription

    def subscribe(self):
        """
        Adds channel to groups for requested objects.
        """
        objects = self.get_objects()

        # Check permissions
        for obj in objects:
            self.check_permissions(obj)

        groups = self.get_group_names(objects, subscribe=True)

        # Add channel to groups
        for group in groups - self.groups:
            async_to_sync(self.consumer.channel_layer.group_add)(
                group,
                self.consumer.channel_name,
            )

        # Track groups
        self.groups.update(groups)

        # Response
        self.send(
            status=status.HTTP_200_OK,
            text={'detail': 'subscription successful'},
        )

    def unsubscribe(self):
        """
        Removes channel from groups for requested objects.
        """
        objects = self.get_objects()
        groups = self.get_group_names(objects)

        # Remove channel from groups
        for group in groups:
            async_to_sync(self.consumer.channel_layer.group_discard)(
                group,
                self.consumer.channel_name,
            )

        # Group tracking
        self.groups.difference_update(groups)

        # Response
        self.send(
            status=status.HTTP_204_NO_CONTENT,
            text={'detail': 'subscription cancelled'},
        )

    # Creation

    def create(self):
        """
        Performs and broadcasts a new database entry.
        """
        serializer = self.deserialize()
        instance = self.perform_create(serializer)
        self.send(
            status=status.HTTP_201_CREATED,
            text={'detail': 'creation successful'},
        )
        self.group_send_post_create(instance)

    def perform_create(self, serializer):
        """
        Performs the creation of the object.
        """
        if 'perform_create' in self._ignore_view:
            return serializer.save()
        else:
            return self.view.perform_create(serializer)

    def group_send_post_create(self, instance):
        """
        Broadcasts new created objects.
        """
        error_msg = "'perform_create' should return the saved instance."
        assert instance is not None, error_msg

        self.group_send(
            self.get_group_name(instance),
            self.serialize(instance),
        )

    # Updates

    def update(self):
        """
        Performs and broadcasts updates of database objects.
        """
        obj = self.get_object()
        # todo: Remove?
        obj.signal_broadcast = False
        serializer = self.deserialize(obj, partial=True)
        instance = self.perform_update(serializer)
        self.send(
            status=status.HTTP_200_OK,
            text={'detail': 'update successful'},
        )
        self.group_send_post_update(instance)

        # todo: Is this necessary?
        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}

    def perform_update(self, serializer):
        """
        Performs the update of the object.
        """
        if 'perform_update' in self._ignore_view:
            return serializer.save(partial=True)
        else:
            return self.view.perform_update(serializer)

    def group_send_post_update(self, instance):
        """
        Broadcasts updated objects.
        """
        error_msg = "'perform_update' should return the saved instance."
        assert instance is not None, error_msg

        self.group_send(
            self.get_group_name(instance),
            self.serialize(instance),
        )

    # Deletion

    def delete(self):
        """
        Performs and broadcasts deletions of database objects.
        """
        obj = self.get_object()
        pk = obj.pk
        obj.signal_broadcast = False
        self.perform_destroy(obj)
        obj.pk = pk
        self.send(
            status=status.HTTP_204_NO_CONTENT,
            text={'detail': 'deletion successful'},
        )
        self.group_send_post_delete(obj)

    def perform_destroy(self, instance):
        """
        Performs the deletion of the object.
        """
        if 'perform_destroy' in self._ignore_view:
            instance.delete()
        else:
            self.view.perform_destroy(instance)

    def group_send_post_delete(self, instance):
        """
        Broadcasts deleted objects.
        """
        self.group_send(self.get_group_name(instance), {'id': instance.pk})

    # Processing

    def serialize(self, obj):
        """
        Returns the serialized data for the object.
        """
        return self.serializer_class(obj).data

    def deserialize(self, obj=None, partial=False):
        """
        Checks permissions and returns a serializer with the data.
        """
        data = self.parse()

        self.check_permissions(obj)

        serializer = self.serializer_class(obj, data=data, partial=partial)
        serializer.is_valid(raise_exception=True)
        return serializer

    def render(self, data):
        """
        Returns the rendered data as utf-8 string.
        """
        return self.renderer.render(data).decode('utf-8')

    def parse(self):
        # parser_classes is set to the DRF default if not set
        # in the view (see APIView code)
        return self.parser.parse(io.BytesIO(self.data)).get('payload')
        # return parser.parse(io.BytesIO(self.data.encode())).get('payload')
        # return parser.parse(io.StringIO(self.data)).get('payload')

    # Authentication

    def perform_authentication(self):
        """
        Does nothing right now.
        """
        # The user is authenticated in Channels.
        return
        # todo: This implementation doesn't make sense.
        if self.authentication_classes:
            for auth in self.authentication_classes:
                user = auth().authenticate(self.user)
                if user:
                    self.user = user
                    return
        else:
            for auth in self.view.authentication_classes:
                if auth is rest_framework_auth.SessionAuthentication:
                    user = SessionAuthentication().authenticate(self.user)
                    if user:
                        self.user = user
                        return
                else:
                    raise NotImplementedError(
                        "'{cls}' should include a 'authentication_classes' "
                        "attribute.".format(cls=self.__class__.__name__)
                    )

    def check_permissions(self, obj):
        """
        Check for DRF permissions and object permissions.
        """
        request = self.get_request(
            user=self.user,
            method=self._action_mapping[self.action][0].upper(),
        )

        for permission in self.view.get_permissions():
            try:
                has_permission = permission.has_permission(request, self.view)
                has_object_permission = permission.has_object_permission(
                    request=request,
                    view=self.view,
                    obj=obj,
                )
            except Exception as e:
                raise NotImplementedError(
                    "'{permission}' might need a custom implementation. "
                    "You could override 'check_permissions' in '{klass}'. "
                    "The original error is:\n{error}: {message}".format(
                        permission=permission.__class__.__name__,
                        klass=self.__class__.__name__,
                        error=e.__class__.__name__,
                        message=e,
                    )
                )
            if not (has_permission and has_object_permission):
                raise exceptions.PermissionDenied()

    # Responses

    def send(self, **kwargs):
        """
        Sends messages to the user.
        """
        async_to_sync(self.consumer.send)(text_data=json.dumps(kwargs))

    def group_send(self, group_name, data):
        """
        Broadcasts changes to the group.
        """
        # self.consumer.channel_layer cannot be used because
        # self.consumer does not always exist when this method gets called
        async_to_sync(get_channel_layer().group_send)(
            group_name,
            {
                'type': 'api.send',
                'text': self.render({
                    'action': self.action,
                    'data': data,
                }),
            },
        )

    def handle_exception(self, exc):
        """
        Reraises the exception or sends a message to the user.
        """
        response = self.view.handle_exception(exc)
        self.send(status=response.status_code, text=response.data)


class ModelBindingMixin:
    """
    Mixin to broadcast model changes not perfomed by the APIConsumer.
    """

    # Signals

    @classmethod
    def register(cls):
        """
        Registers post save and delete signals for the `model`.
        """
        # If view is None directly on the class, assume it's abstract.
        if cls.view is None:
            if 'view' in cls.__dict__:
                return
            else:
                raise ValueError(
                    "'{cls}' should include a 'view' attribute.".format(
                        cls=cls.__name__
                    )
                )
        # Connect signals
        model = cls.get_model()
        post_save.connect(cls.post_save_receiver, sender=model)
        post_delete.connect(cls.post_delete_receiver, sender=model)

    @classmethod
    def post_save_receiver(cls, instance, created, **kwargs):
        cls.post_change_receiver(instance, CREATE if created else UPDATE)

    @classmethod
    def post_delete_receiver(cls, instance, **kwargs):
        cls.post_change_receiver(instance, DELETE)

    @classmethod
    def post_change_receiver(cls, instance, action):
        """
        Triggers the binding to possibly send to its group.
        """
        if getattr(instance, 'signal_broadcast', True):
            # We don't need the immediate broadcast features
            self = cls(immediate_broadcast=False)
            self.action = action

            getattr(self, 'group_send_post_{}'.format(action))(instance)


class APIConsumer(SelfContainedAPIConsumer, ModelBindingMixin):
    """
    Real time API endpoint.
    """


class GroupUserConsumer(AsyncWebsocketConsumer):
    """
    Consumer which puts all channels of a user into a group.
    """

    async def websocket_connect(self, message):
        """
        Tracks user channel association.
        """
        self.groups.append(
            # Use '' because the pk is required and therefore won't clash
            # with another pk.
            get_group_user_key(getattr(self.scope.get('user'), 'pk', ''))
        )
        await super().websocket_connect(message)


class AlreadyRegistered(Exception):
    pass


class APIDemultiplexer(GroupUserConsumer):
    """
    Channels consumer which instanciates API endpoints and passes messages on.
    """
    _registry = {}

    @classmethod
    def register(cls, *consumers):
        """
        Registers an API endpoint to be used.
        """
        for consumer in consumers:
            assert consumer.stream is not None, (
                "'{cls}' should include a 'stream' attribute.".format(
                    cls=consumer.__name__
                )
            )
            if consumer.stream in cls._registry:
                raise AlreadyRegistered(
                    "The name '{name}' is registered for '{consumer}' "
                    "already.".format(
                        name=consumer.stream,
                        consumer=cls._registry[consumer.stream].__name__,
                    )
                )
            cls._registry[consumer.stream] = consumer
            consumer.register()

    def __init__(self, scope, *args, **kwargs):
        """
        Initializes all API endpoints.
        """
        super().__init__(scope, *args, **kwargs)
        self._consumers = {s: c(self) for s, c in self._registry.items()}

    async def receive(self, text_data=None, bytes_data=None):
        """
        Handles incoming messages.
        """
        try:  # TODO
            url = json.loads(text_data)['stream'].split('/')
        except TypeError:
            url = self.scope['path'].split('/')
        consumer = self._consumers.get(url[0], None)
        if consumer is None:
            await self.send(text_data=json.dumps({
                'status': status.HTTP_404_NOT_FOUND,
                'detail': 'Not found',
            }))
        else:
            await database_sync_to_async(consumer.receive)(
                url,
                text_data,
                bytes_data,
            )

    async def api_send(self, event):
        """
        Sends a message.
        """
        await self.send(text_data=event['text'])
        # await self.send(bytes_data=event['text'])

    async def websocket_disconnect(self, message):
        """
        Discards channel from groups.
        """
        for consumer in self._consumers.values():
            self.groups.extend(consumer.groups)

        await super().websocket_disconnect(message)
