from django import forms

from .models import Bus, School, Student


class StudentForm(forms.ModelForm):
    """Formulaire simplifié : la position GPS du domicile est définie par le
    parent lors de sa première connexion (onboarding), pas par l'école."""

    class Meta:
        model = Student
        fields = [
            "matricule",
            "nom",
            "postnom",
            "prenom",
            "address",
            "parent_phone",
        ]


class BusForm(forms.ModelForm):
    """Formulaire bus : le compte chauffeur (driver_user) n'est plus géré ici —
    il est lié via l'admin Django ou le seed."""

    class Meta:
        model = Bus
        fields = ["code_bus", "capacity", "driver_name", "is_in_service"]


class LoginForm(forms.Form):
    matricule = forms.CharField(
        label="Matricule",
        max_length=50,
        widget=forms.TextInput(
            attrs={"placeholder": "Ex : MAT0001", "autofocus": True}
        ),
    )
    password = forms.CharField(
        label="Mot de passe",
        widget=forms.PasswordInput(attrs={"placeholder": "Votre mot de passe"}),
    )


class SchoolForm(forms.ModelForm):
    class Meta:
        model = School
        fields = ["code_ecole", "name", "address", "latitude", "longitude"]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any"}),
            "longitude": forms.NumberInput(attrs={"step": "any"}),
        }


class SchoolRegisterForm(forms.ModelForm):
    """Création d'une nouvelle école + compte administrateur."""
    password = forms.CharField(
        label="Mot de passe",
        min_length=8,
        widget=forms.PasswordInput(attrs={"placeholder": "Au moins 8 caractères"}),
    )
    confirm_password = forms.CharField(
        label="Confirmer le mot de passe",
        widget=forms.PasswordInput(attrs={"placeholder": "Répétez le mot de passe"}),
    )

    class Meta:
        model = School
        fields = ["code_ecole", "name", "address", "latitude", "longitude"]
        widgets = {
            "latitude": forms.NumberInput(attrs={"step": "any", "id": "id_latitude"}),
            "longitude": forms.NumberInput(attrs={"step": "any", "id": "id_longitude"}),
            "code_ecole": forms.TextInput(attrs={"placeholder": "Ex : ECO002", "id": "id_code_ecole"}),
            "name": forms.TextInput(attrs={"placeholder": "Nom complet de l'école"}),
            "address": forms.TextInput(attrs={"placeholder": "Adresse physique (ex : Av. du Marché, Lubumbashi)", "id": "id_address"}),
        }

    def clean_code_ecole(self):
        code = self.cleaned_data["code_ecole"].strip().upper()
        qs = School.objects.filter(code_ecole=code)
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Le code école existe déjà.")
        return code

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("password") != cleaned.get("confirm_password"):
            self.add_error("confirm_password", "Les mots de passe ne correspondent pas.")
        return cleaned
