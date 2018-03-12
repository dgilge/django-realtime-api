=============
Real time API
=============

A Django API app which supports WebSockets (long polling might be added). It aims to support the Django REST framework out of the box (with limitations).

I coded it for one of our projects and I thought why not make it available to others. However, I'm not very happy about the current implementation because it doesn't pay respect to all REST framework attributes/methods which might be quite buggy. Maybe we can make something nice out of it or it inspires somebody to make something better. Please be careful when you use it.

.. warning::
   Please note that this package is in development and not suitable for production!


Features
========

Django REST framework
---------------------

* Subscribe to changes
* Creation
* Updates
* Deletion

Not supported so far:
.....................

* Listing
* Gets
* Options
* Any format other than JSON

General realtime API features
-----------------------------

Disconnecting WebSockets when a user or authentications changes
...............................................................

Normally the user object is set at the establishment of a scope and won't change if the user object changes or the user logs in/out. You can use the ``GroupUserConsumer`` in connection with the provided signals to change that.

``GroupUserConsumer`` tracks all channels which a user has. The signals disconnect all corresponding WebSockets.

I plan to separate the REST framework specific things from the general stuff in order to provide an API class with base functions like subscription.

Testing
.......

Some features you might find helpful:

* ``AuthWebsocketCommunicator`` logs in users automatically. Use it like this::

     communicator = await AuthWebsocketCommunicator(Consumer, 'path', user=user)

  The class includes some helpful asynchronous methods, they don't change the scope, though:

     * ``login(**credentials)``
     * ``force_login(user, backend=None)``
     * ``logout()``
     * ``queue_empty(timeout=0.1)`` – if there are messages to receive
     * ``queue_count(wait=0.1)`` - how many messages wait to be received

* ``create_user(username=None, password='pw', **kwargs)`` returns a user object. Usage::

    user = await create_user(first_name='Alex')

* Fixtures ``user`` and ``admin``. Usage::

    async def test_some_stuff(user, admin):
        result = do_something(user=user)
        assert result.owner == user.username
        assert result.supervisor == admin.username

Dependencies
============

* Python 3.5 and higher
* Django 2.0 (Django 1.11 might also work but is not tested)
* Channels 2.0
* Django REST framework 3.7 (if you want to use it)

Quick start
===========

1. Get real time API::

   pip install -e git:
   pipenv install git+https://github.com/dgilge/.git#egg=-2.0.2

The package is not available on PyPI yet. If there are several people who want to use it I will make it available. Just let me know.

2. Add "realtime-api" to your INSTALLED_APPS setting like this::

   INSTALLED_APPS = [
       ...
       'channels',
       'djangorestframework',
       'realtime-api',
   ]

3. Create a consumer for each Django REST framework view (or viewset) you want to have a WebSocket end point for. ``stream`` is the first part of the URL. You may store them in a ``consumers.py`` module in your app. For instance::

   from realtime_api.consumers import APIConsumer

   class MyRealTimeConsumer(APIConsumer):
       view = MyAPIView
       stream = 'my-api'

4. Register the consumers like this::

   from realtime_api.consumers import APIDemultiplexer

   APIDemultiplexer.register(MyRealTimeConsumer, MyOtherConsumer)

5. Define a routing (for instance in ``routing.py`` in your project folder, where ``urls.py`` lives, too)::

   from channels.routing import ProtocolTypeRouter, URLRouter
   from channels.security.websocket import AllowedHostsOriginValidator
   from django.conf.urls import url
   from realtime_api.consumers import APIDemultiplexer

   application = ProtocolTypeRouter({
       'websocket': AllowedHostsOriginValidator(
           URLRouter([
               url('^api/$', APIDemultiplexer),
           ])
       ),
   })

You might also want add the ``AuthMiddlewareStack``. More details are available in the Channels documentation.

6. Start the development server with ``python manage.py runserver`` and you are ready to communicate with the API endpoint. Read on for details.

   One thing probably want to override is ``get_group_name()``.

Actions
=======

Subscription
------------

Send a JSON string to ``/<stream>/subscribe/`` with any field you have specified in your serializer you want to receive updates for::

   {
     "id": 1
   }

Now you will receive any* changes made to the object in an almost equal (see limitations) JSON structure as you receive it in a GET response by the Django REST framework.

In order to cancel the subscription send the same JSON object to ``/<stream>/unsubscribe/``.

You can also define other lookups by including a ``subscription_field_mapping`` in your consumer. For instance::

   subscription_field_mapping = {
       'ids': 'pk__in',
       'name': 'name__istartswith',
   }

\*= This is done inside the consumer or via Django's signals and has therefore following side effect.

.. warning::
   You do not receive changes performed by ``update`` or bulk operations.

Create
------

Send a JSON string to ``/<stream>/create`` in the same format as you use it in the Djang REST framework.

Update
------

Send a JSON string to ``/<stream>/update/<pk>/``.

Delete
------

Send an empty JSON string (``{}``) to ``/<stream>/delete/<pk>/``.

Alternatively to the path you can send an equal ``stream`` value within your JSON object.

.. note::
   One of these implementations (path/stream value) will probably be removed in the future.

APIConsumer
===========

.. note::
   The ``APIConsumer`` is no Channels consumer. The reason for this name is that I plan to convert it to a Channels consumer when demultiplexing is implemented.

Some things you might to override:

Attributes
----------

view
....

Required, a subclass of ``APIView``. For instance ``ModelViewSet``.

stream
......

Required, the first part of the path.

model
.....

Required if you don't include a ``queryset`` in your view.

allowed_actions
...............

Here you can specify the actions (as tuple or list) you want to allow if they differ from the allowed methods in the view. Possible values are ``create``, ``update``, ``delete`` (equivalent to the methods ``POST``, ``PUT``/``PATCH``, ``DELETE``).

lookup_field
............

Defaults to ``pk``.

serializer_class
................

If you don't want to use the view's ``serializer_class``.

Methods
-------

get_group_name
..............

The default implementation is a group for each consumer's ``stream`` and object's ``pk``.

Updates are used groups for broadcasting. When a object changes will be serialized and sent to all users (channels) in a group.

Probably you want wider groups. For instance you have a ``Comment`` model with a foreign key to the ``Topic`` model. In order to create one group for each ``Topic`` you could use::

   def get_group_names(self, obj):
       return '{}-{}'.format(self.stream, obj.topic_id)

perform_authentication
......................

If you need a special authentication.

A note on the design
--------------------

A Channels consumer instance has a lifetime equal to the WebSocket connection time. I wanted to retain this design. Therefore your view is initialized on connection and remains for the whole scope. However, this makes the implementation not easier.

Limitations
===========

* Multiple view attributes and methods don't have any effect in the consumer. Check if you override them in your view and customize your consumer where needed! For details see below.
* The view's request instance is a fake and has only a user attribute. (Permissions get the method additionally.)
* URLs are relative in the JSON objects.

Modifications to your API views
===============================

Your view might be suitable as it is.

However, if you overrode ``perform_create`` or ``perform_update`` your methods should return the saved instance. Alternatives are to override the methods of the same names in your ``APIConsumer`` subclass or include the ``immediate_broadcast`` attribute and set it to ``False``.


Used API view attributes and methods
====================================

Attributes
----------

They are not used directly but via the view's methods.

* ``parser_classes``
* ``permission_classes``
* ``queryset``
* ``renderer_classes``
* ``serializer_class``
* ``settings``

Methods
-------

* (``get_authenticate_header``)
* (``get_authenticators``)
* ``get_exception_handler``
* ``get_exception_handler_context``
* ``get_parsers``
* ``get_permissions``
* ``get_queryset``
* ``get_renderers``
* ``get_serializer`` -> Implement that correctly!
* ``get_serializer_class``
* ``get_serializer_context`` -> Implement that correctly!
* ``handle_exception``
* ``perform_create``
* ``perform_destroy``
* ``perform_update``
* ``raise_uncaught_exception``

Not used API view attributes and methods
========================================

Attributes
----------

* ``allowed_methods``
* ``authentication_classes``
* ``content_negotiation_class``
* ``default_response_headers``
* ``filter_backends`` (!)
* ``http_method_names``
* ``lookup_field`` -> Maybe use it in the consumer?
* ``lookup_url_kwarg`` -> Maybe use it in the consumer?
* ``metadata_class``
* ``pagination_class``
* ``paginator``
* ``schema``
* ``throttle_classes``
* ``versioning_class``

Methods
-------

Many of these are not used because of not having a proper request instance.

* ``_allowed_methods``
* ``as_view``
* ``check_object_permissions``
* ``check_permissions``
* ``check_throttles``
* ``create``
* ``destroy``
* ``determine_version``
* ``dispatch``
* ``get``
* ``post``
* ``put``
* ``patch``
* ``delete``
* ``filter_queryset`` (!)
* ``finalize_response``
* ``get_content_negotiator``
* ``get_format_suffix``
* ``get_object`` (!)
* ``get_paginated_response``
* ``get_parser_context``
* ``get_renderer_context``
* ``get_success_headers``
* ``get_throttles``
* ``get_view_description``
* ``get_view_name``
* ``http_method_not_allowed``
* ``initial``
* ``initialize_request``
* ``list``
* ``options``
* ``paginate_queryset``
* ``partial_update``
* ``perform_authentication``
* ``perform_content_negotiation``
* ``permission_denied``
* ``retrieve``
* ``throttled``
* ``update``

You can have a look at `cdrf.co <http://www.cdrf.co/3.7/>`_ on how they play together.

ToDo
====

* JSON object design decisions
* Separate the DRF specific implementations from the other API consumer code
* Support nested routing (DRF extensions)
* Support Django Guardian (e.g. AnonymousUser in login signal)
* Checking permissions (e.g. at subscription) allows you to get information whether it is in the database (you get a 403) or not (you get a 404). This is a security leak (e.g. by cancelling subscription with ``{'email': 'me@example.com'}``).
