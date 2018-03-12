from realtime_api.consumers import (
    APIConsumer, SelfContainedAPIConsumer, GroupUserConsumer
)

from . import views


class SelfContainedTestConsumer(SelfContainedAPIConsumer):
    view = views.TestViewSet
    stream = 'sc'


class ExceptionToMessageConsumer(APIConsumer):
    view = views.TestViewSet

    def __init__(self, *args, **kwargs):
        self.messages = []
        super().__init__(*args, **kwargs)

    def handle_exception(self, err):
        self.messages.append(err)


class StandardConsumer(APIConsumer):
    view = views.TestViewSet
    stream = 'standard'


class MappingConsumer(APIConsumer):
    view = views.TestViewSet
    stream = 'mapping'
    subscription_field_mapping = {
        'counter_max': 'counter__lte',
        'names': 'name__in',
    }


class TestConsumer(APIConsumer):
    view = views.TestViewSet
    stream = 'test-obj'

    def get_group_name(self, pk):
        """
        All objects are in the same group.
        """
        return self.stream


class ProxyConsumer(TestConsumer):
    view = views.ProxyViewSet
    stream = 'proxy'


class PerformUpdateConsumer(ExceptionToMessageConsumer):
    view = views.TestViewSet
    stream = 'perform'

    def perform_update(self, serializer):
        pass


class PerformCreateUpdateDeleteConsumer(PerformUpdateConsumer):
    view = views.TestViewSet

    def perform_create(self, serializer):
        pass

    def perform_destroy(self, instance):
        pass


class SessionAuthenticationConsumer(TestConsumer):
    view = views.SessionAuthenticationViewSet


class PermissionConsumer(APIConsumer):
    view = views.PermissionViewSet
    stream = 'permissions'


class GroupTestConsumer(GroupUserConsumer):
    async def disconnect(self, code):
        await self.send('Socket closes with code {}'.format(code))


class DummyConsumer:
    scope = {}

    async def send(self, *args, **kwargs):
        pass
