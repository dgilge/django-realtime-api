from django.apps import AppConfig


class RealTimeAPIConfig(AppConfig):
    name = 'realtime_api'
    verbose_name = 'Django real time API'

    def ready(self):
        # Register signals
        from . import signals  # noqa: F401
