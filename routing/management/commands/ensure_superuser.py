"""Crée le superuser par défaut s'il n'existe pas encore.

Utile pour le déploiement automatique sur Render : la commande est
exécutée pendant le build pour garantir qu'un compte admin est toujours
disponible après le premier déploiement.
"""
import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Crée le superuser par défaut s'il n'existe pas."

    def handle(self, *args, **options):
        username = os.environ.get("SUPERUSER_USERNAME", "mamelk")
        email = os.environ.get("SUPERUSER_EMAIL", "mamelk@scolaloop.com")
        password = os.environ.get("SUPERUSER_PASSWORD", "Scolaloop2026!")

        if User.objects.filter(username=username).exists():
            self.stdout.write(f"Le superuser '{username}' existe deja.")
            return

        User.objects.create_superuser(username, email, password)
        self.stdout.write(self.style.SUCCESS(f"Superuser '{username}' cree avec succes."))
