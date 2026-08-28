from django.db import migrations, models


def backfill_matricule(apps, schema_editor):
    """Attribue un matricule unique aux élèves existants (MAT0001, MAT0002, ...)."""
    Student = apps.get_model("routing", "Student")
    for index, student in enumerate(Student.objects.order_by("id"), start=1):
        student.matricule = f"MAT{index:04d}"
        student.save(update_fields=["matricule"])


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0002_student_assigned_route"),
    ]

    operations = [
        migrations.RenameField(
            model_name="student",
            old_name="first_name",
            new_name="prenom",
        ),
        migrations.RenameField(
            model_name="student",
            old_name="last_name",
            new_name="nom",
        ),
        migrations.AddField(
            model_name="student",
            name="postnom",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="student",
            name="matricule",
            field=models.CharField(default="", max_length=50),
            preserve_default=False,
        ),
        migrations.RunPython(backfill_matricule, reverse_code=migrations.RunPython.noop),
        migrations.AlterField(
            model_name="student",
            name="matricule",
            field=models.CharField(max_length=50, unique=True),
        ),
    ]
