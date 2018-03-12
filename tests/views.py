from django.shortcuts import render
from rest_framework import (
    authentication, generics, permissions, viewsets
)
from rest_framework.exceptions import MethodNotAllowed
from djangorestframework_camel_case.parser import CamelCaseJSONParser
from djangorestframework_camel_case.render import CamelCaseJSONRenderer

from . import models, serializers


class TestViewSet(viewsets.ModelViewSet):
    queryset = models.APIModel.objects.all()
    serializer_class = serializers.TestSerializer
    # This is right now the only implementation. Therefore I set it here:
    authentication_classes = (authentication.SessionAuthentication,)
    parser_classes = (CamelCaseJSONParser,)
    renderer_classes = (CamelCaseJSONRenderer,)


class ProxyViewSet(TestViewSet):
    queryset = models.ProxyModel.objects.all()
    serializer_class = serializers.ProxySerializer


class PerformCreateUpdateViewSet(TestViewSet):
    def perform_create(self, serializer):
        return serializer.save()

    def perform_update(self, serializer):
        return serializer.save()


class PerformCreateUpdateDeleteViewSet(PerformCreateUpdateViewSet):
    def perform_destroy(self, instance):
        instance.delete()
        # return instance


class SessionAuthenticationViewSet(TestViewSet):
    authentication_classes = (authentication.SessionAuthentication,)


class PermissionViewSet(TestViewSet):
    permission_classes = (
        permissions.AllowAny,
        permissions.IsAuthenticated,
        permissions.IsAdminUser,
        permissions.DjangoModelPermissions,
        permissions.DjangoObjectPermissions,
    )


class UpdateView(generics.UpdateAPIView):
    queryset = models.APIModel.objects.all()
    serializer_class = serializers.TestSerializer


def ws_view(request):
    if request.method == 'GET':
        return render(request, 'websockets_tests.html')
    raise MethodNotAllowed(request.method)
