import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("routing", "0004_alter_student_options"),
    ]

    operations = [
        migrations.CreateModel(
            name="School",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=100)),
                ("latitude", models.FloatField()),
                ("longitude", models.FloatField()),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.RenameField(
            model_name="bus",
            old_name="registration_number",
            new_name="code_bus",
        ),
        migrations.AddField(
            model_name="student",
            name="is_frozen",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="route",
            name="school",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="routes",
                to="routing.school",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="students_taken",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="route",
            name="students_remaining",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
