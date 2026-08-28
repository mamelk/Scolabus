from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .models import Student


class MatriculeBackend(ModelBackend):
    """Authentifie un utilisateur via le matricule de l'élève associé.

    Le champ « username » du formulaire contient le matricule (ex. MAT0001) :
    on retrouve l'élève par son matricule, puis son compte utilisateur lié,
    et on vérifie le mot de passe.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if username is None or password is None:
            return None

        student = (
            Student.objects.filter(matricule__iexact=username.strip())
            .select_related("user")
            .first()
        )
        user = student.user if student else None
        if (
            user is not None
            and user.check_password(password)
            and self.user_can_authenticate(user)
        ):
            return user
        return None

    def get_user(self, user_id):
        User = get_user_model()
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
