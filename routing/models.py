from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


LATITUDE_VALIDATORS = [MinValueValidator(-90.0), MaxValueValidator(90.0)]
LONGITUDE_VALIDATORS = [MinValueValidator(-180.0), MaxValueValidator(180.0)]


def _local_date():
    """Date locale du jour (utilisée comme défaut de champ sérialisable)."""
    return timezone.localdate()


class School(models.Model):
    """École : point de départ/retour des trajets et conteneur des données."""
    code_ecole = models.CharField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    address = models.CharField(max_length=255, blank=True)
    latitude = models.FloatField(validators=LATITUDE_VALIDATORS)
    longitude = models.FloatField(validators=LONGITUDE_VALIDATORS)
    is_active = models.BooleanField(
        default=True,
        help_text="False si l'école est désactivée par l'administration générale "
        "(accès bloqué, mais données conservées).",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="school",
        help_text="Compte administrateur de l'école (identifiant = code_ecole).",
    )
    created_at = models.DateTimeField(
        auto_now_add=True, null=True, blank=True, help_text="Date de création de l'école."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.code_ecole})"


class Student(models.Model):
    matricule = models.CharField(max_length=50, unique=True)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="students",
    )
    must_change_password = models.BooleanField(
        default=True,
        help_text="True si l'élève doit personnaliser son mot de passe à la première connexion.",
    )
    prenom = models.CharField(max_length=100)
    postnom = models.CharField(max_length=100, blank=True)
    nom = models.CharField(max_length=100)
    address = models.CharField(max_length=255)
    latitude = models.FloatField(
        validators=LATITUDE_VALIDATORS,
        null=True,
        blank=True,
        help_text="Position GPS du domicile, définie par le parent lors de sa première connexion.",
    )
    longitude = models.FloatField(
        validators=LONGITUDE_VALIDATORS,
        null=True,
        blank=True,
        help_text="Position GPS du domicile, définie par le parent lors de sa première connexion.",
    )
    is_active = models.BooleanField(default=True)
    is_frozen = models.BooleanField(default=False)
    is_taken = models.BooleanField(
        default=False,
        help_text="True si l'élève a été pris en charge par le bus (ramassage confirmé).",
    )
    overcapacity_alert = models.BooleanField(
        default=False,
        help_text="True si l'élève a une position GPS mais qu'aucun bus actif de son école "
        "n'a de capacité disponible (tous les bus proches sont complets).",
    )
    parent_notified = models.BooleanField(
        default=False,
        help_text="True si un SMS d'approche (< 100 m) a déjà été envoyé au parent.",
    )
    parent_phone = models.CharField(max_length=20, blank=True, help_text="Téléphone du parent (ex. +243...).")
    parent_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="children",
        help_text="Compte de connexion du parent (facultatif — l'élève peut se connecter avec son matricule).",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="student",
        help_text="Compte de connexion associé (matricule = identifiant).",
    )
    assigned_route = models.ForeignKey(
        "Route",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="students",
    )

    class Meta:
        ordering = ["nom", "prenom"]

    def __str__(self):
        return " ".join(filter(None, [self.prenom, self.postnom, self.nom]))


class Bus(models.Model):
    code_bus = models.CharField(max_length=50)
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="buses",
    )
    must_change_password = models.BooleanField(
        default=True,
        help_text="True si le chauffeur doit personnaliser son mot de passe à la première connexion.",
    )
    capacity = models.IntegerField()
    driver_name = models.CharField(max_length=100, blank=True)
    is_in_service = models.BooleanField(default=True)
    last_latitude = models.FloatField(
        null=True, blank=True, help_text="Dernière latitude GPS reçue du téléphone du chauffeur."
    )
    last_longitude = models.FloatField(
        null=True, blank=True, help_text="Dernière longitude GPS reçue du téléphone du chauffeur."
    )
    last_position_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Horodatage de la dernière position GPS reçue."
    )
    speed_kmh = models.FloatField(
        default=0.0,
        help_text="Dernière vitesse connue du bus (km/h), envoyée par le téléphone du chauffeur.",
    )
    driver_connected = models.BooleanField(
        default=False,
        help_text="True si le chauffeur est connecté (le bus apparaît alors en direct "
        "sur la carte de l'école ; il disparaît à la déconnexion).",
    )
    driver_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="driven_bus",
        help_text="Compte de connexion du chauffeur associé à ce bus.",
    )

    def __str__(self):
        return self.code_bus


class BusStop(models.Model):
    name = models.CharField(max_length=100)
    latitude = models.FloatField(validators=LATITUDE_VALIDATORS)
    longitude = models.FloatField(validators=LONGITUDE_VALIDATORS)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Route(models.Model):
    name = models.CharField(max_length=100)
    bus = models.ForeignKey(
        Bus,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routes",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="routes",
    )
    total_distance_km = models.FloatField(default=0.0)
    estimated_duration_minutes = models.FloatField(default=0.0)
    students_taken = models.PositiveIntegerField(default=0)
    students_remaining = models.PositiveIntegerField(default=0)
    path_geometry = models.JSONField(
        null=True,
        blank=True,
        help_text="Points [lat, lon] de l'itinéraire routier réel (OSRM), au format JSON.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RouteStop(models.Model):
    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name="stops",
    )
    stop = models.ForeignKey(BusStop, on_delete=models.CASCADE)
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ["route", "order"]
        constraints = [
            models.UniqueConstraint(
                fields=["route", "order"],
                name="unique_stop_order_per_route",
            ),
        ]

    def __str__(self):
        return f"{self.route} — {self.stop} (#{self.order})"


class Absence(models.Model):
    """Absence déclarée par le parent : l'élève est exclu des tournées le jour même."""
    student = models.ForeignKey(Student, on_delete=models.CASCADE, related_name="absences")
    date = models.DateField(default=_local_date)
    reason = models.CharField(max_length=255, blank=True, help_text="Motif optionnel (ex : maladie).")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["student", "date"], name="unique_absence_per_day"),
        ]

    def __str__(self):
        return f"Absence {self.student} — {self.date}"


class Incident(models.Model):
    """Incident signalé par le chauffeur ou détecté automatiquement (excès de vitesse)."""
    TYPE_EMBOUTEILLAGE = "Embouteillage"
    TYPE_PANNE = "Panne"
    TYPE_ACCIDENT = "Accident"
    TYPE_VITESSE = "Excès de vitesse"
    TYPE_AUTRE = "Autre"
    INCIDENT_TYPES = [
        (TYPE_EMBOUTEILLAGE, "Embouteillage"),
        (TYPE_PANNE, "Panne"),
        (TYPE_ACCIDENT, "Accident"),
        (TYPE_VITESSE, "Excès de vitesse"),
        (TYPE_AUTRE, "Autre"),
    ]

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="incidents")
    type_incident = models.CharField(max_length=30, choices=INCIDENT_TYPES)
    description = models.TextField(blank=True, help_text="Description libre (optionnelle).")
    timestamp = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.type_incident} — {self.bus.code_bus}"


class GPSLog(models.Model):
    """Point de passage GPS enregistré à chaque mise à jour de position du bus."""
    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="gps_logs")
    latitude = models.FloatField()
    longitude = models.FloatField()
    speed_kmh = models.FloatField(default=0.0, help_text="Vitesse instantanée estimée (km/h).")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]
        indexes = [models.Index(fields=["bus", "timestamp"])]

    def __str__(self):
        return f"GPS {self.bus.code_bus} @ {self.timestamp:%H:%M:%S}"


class BusMaintenance(models.Model):
    """Intervention d'entretien / révision technique sur un bus."""
    SERVICE_TYPES = [
        ("Vidange", "Vidange"),
        ("Freins", "Freins"),
        ("Pneumatiques", "Pneumatiques"),
        ("Contrôle technique", "Contrôle technique"),
        ("Autre", "Autre"),
    ]

    bus = models.ForeignKey(Bus, on_delete=models.CASCADE, related_name="maintenance_records")
    service_type = models.CharField(max_length=30, choices=SERVICE_TYPES)
    cost = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    date_effectuee = models.DateField(default=_local_date)
    prochaine_echeance_km_ou_date = models.CharField(
        max_length=100, blank=True, help_text="Ex : 5000 km ou 15/12/2026."
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_effectuee", "-created_at"]

    def __str__(self):
        return f"{self.service_type} — {self.bus.code_bus}"
