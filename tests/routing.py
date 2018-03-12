from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf.urls import url

from realtime_api.consumers import APIDemultiplexer

from . import consumers


APIDemultiplexer.register(consumers.TestConsumer)

application = ProtocolTypeRouter({
    'websocket': AuthMiddlewareStack(URLRouter([
        url('^api/$', APIDemultiplexer),
    ])),
})
