from django.db import models


class BaseTestModel(models.Model):
    class Meta:
        app_label = 'tests'
        abstract = True


class APIModel(BaseTestModel):
    name = models.CharField(max_length=100)
    counter = models.SmallIntegerField()

    class Meta:
        verbose_name = 'API Test Model'
        get_latest_by = 'id'


class ProxyModel(APIModel):
    class Meta:
        proxy = True
