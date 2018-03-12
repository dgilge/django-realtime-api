from rest_framework import serializers

from . import models


class TestSerializer(serializers.ModelSerializer):
    class Meta:
        model = models.APIModel
        fields = ('id', 'name', 'counter')


class ProxySerializer(TestSerializer):
    class Meta(TestSerializer.Meta):
        model = models.ProxyModel
