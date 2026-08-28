import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def backfill_existing_data(apps, schema_editor):
    """Attribue l'école existante aux données en place et fixe son code."""
    School = apps.get_model("routing", "School")
    Student = apps.get_model("routing", "Student")
    Bus = apps.get_model("routing", "Bus")

    school = School.objects.order_by("id").first()
    if school is None:
        return
    if not school.code_ecole:
        school.code_ecole = "ECO001"
        school.save(update_fields=["code_ecole"])
    Student.objects.filter(school__isnull=True).update(school=school)
    Bus.objects.filter(school__isnull=True).update(school=school)


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0011_student_parent_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="school",
            name="code_ecole",
            field=models.CharField(max_length=50, null=True),
        ),
        migrations.AddField(
            model_name="school",
            name="address",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="school",
            name="user",
            field=models.OneToOneField(
                blank=True,
                help_text="Compte administrateur de l'école (identifiant = code_ecole).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="school",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="school",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="students",
                to="routing.school",
            ),
        ),
        migrations.AddField(
            model_name="student",
            name="must_change_password",
            field=models.BooleanField(
                default=True,
                help_text="True si l'élève doit personnaliser son mot de passe à la première connexion.",
            ),
        ),
        migrations.AddField(
            model_name="bus",
            name="school",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="buses",
                to="routing.school",
            ),
        ),
        migrations.AddField(
            model_name="bus",
            name="must_change_password",
            field=models.BooleanField(
                default=True,
                help_text="True si le chauffeur doit personnaliser son mot de passe à la première connexion.",
            ),
        ),
        migrations.RunPython(backfill_existing_data, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="school",
            name="code_ecole",
            field=models.CharField(max_length=50, unique=True),
        ),
    ]
