from django.apps import AppConfig


class RoutingConfig(AppConfig):
    name = 'routing'

    def ready(self):
        # Enregistre les signaux (affectation automatique des élèves).
        from . import signals  # noqa: F401
