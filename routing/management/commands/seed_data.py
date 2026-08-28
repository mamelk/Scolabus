from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from routing.models import Bus, School, Student

# Coordonnées fictives autour de Lubumbashi (-11.66, 27.47)
# (matricule, prenom, postnom, nom, adresse, latitude, longitude, parent_phone)
STUDENTS = [
    ("MAT0001", "Jean", "Kasongo", "Kabongo", "Av. de la Kasapa, Q. Bel-Air", -11.6647, 27.4794, "+243970000001"),
    ("MAT0002", "Marie", "Ilunga", "Mwamba", "Av. Kalemie, Q. Kamalondo", -11.6550, 27.4860, "+243970000002"),
    ("MAT0003", "Paul", "Kalenga", "Ilunga", "Av. du Golf, Q. Golf", -11.6725, 27.4720, "+243970000003"),
    ("MAT0004", "Grace", "Nkulu", "Tshimanga", "Av. Kasenga, Q. Kenya", -11.6600, 27.4960, "+243970000004"),
    ("MAT0005", "Daniel", "Mbuyi", "Kalenga", "Av. Lualaba, Q. Lualaba", -11.6780, 27.4810, "+243970000005"),
    ("MAT0006", "Esther", "Kabongo", "Nkulu", "Av. Kilela Balanda, Q. Kiwele", -11.6480, 27.4670, "+243970000006"),
    ("MAT0007", "David", "Tshimanga", "Mbuyi", "Av. Bongolo, Q. Makomeno", -11.6710, 27.5010, "+243970000007"),
    ("MAT0008", "Sarah", "Kalonji", "Mutombo", "Av. Likasi, Q. Kalebuka", -11.6850, 27.4640, "+243970000008"),
]


class Command(BaseCommand):
    help = "Alimente la base avec un admin, 2 bus et 8 élèves de démonstration."

    def handle(self, *args, **options):
        # 1. École (point de départ / retour des trajets, code d'accès ECO001)
        school, created = School.objects.get_or_create(
            code_ecole="ECO001",
            defaults={
                "name": "École Scolaloop",
                "address": "Av. de la Kasapa, Lubumbashi",
                "latitude": -11.6647,
                "longitude": 27.4794,
            },
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f"École créée : {school.name} ({school.code_ecole})"))
        else:
            self.stdout.write(self.style.WARNING(f"École déjà présente : {school.name} ({school.code_ecole})"))

        # 2. Compte administrateur
        User = get_user_model()
        if User.objects.filter(username="admin").exists():
            self.stdout.write(self.style.WARNING("Admin déjà présent, ignoré."))
        else:
            User.objects.create_superuser(
                username="admin",
                email="admin@example.com",
                password="admin123",
            )
            self.stdout.write(self.style.SUCCESS("Admin créé : admin / admin123"))

        # 3. Bus (rattachés à l'école)
        buses = [("Bus 01", 10), ("Bus 02", 15)]
        for code_bus, capacity in buses:
            bus, created = Bus.objects.get_or_create(
                code_bus=code_bus,
                defaults={
                    "capacity": capacity,
                    "driver_name": "",
                    "school": school,
                },
            )
            if bus.school_id != school.id:
                bus.school = school
                bus.save(update_fields=["school"])
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f"Bus créé : {bus.code_bus} (capacité {bus.capacity})")
                )
            else:
                self.stdout.write(self.style.WARNING(f"Bus déjà présent : {bus.code_bus}"))

        # 4. Compte chauffeur démo (CHAUF01 / CHAUF01) lié au Bus 02
        driver, driver_created = User.objects.get_or_create(username="CHAUF01")
        if driver_created:
            driver.set_password("CHAUF01")
            driver.save()
        bus_02 = Bus.objects.filter(code_bus="Bus 02").first()
        if bus_02 is not None and bus_02.driver_user_id != driver.id:
            bus_02.driver_user = driver
            if not bus_02.driver_name:
                bus_02.driver_name = "M. Kalonji"
            bus_02.save(update_fields=["driver_user", "driver_name"])
            self.stdout.write(self.style.SUCCESS(f"Chauffeur CHAUF01 lié au {bus_02.code_bus}"))
        # Compte de démo : pas de changement de mot de passe imposé (données de test).
        bus_02.must_change_password = False
        bus_02.save(update_fields=["must_change_password"])

        # 4. Élèves actifs + comptes de connexion (matricule / matricule)
        for matricule, prenom, postnom, nom, address, latitude, longitude, parent_phone in STUDENTS:
            student, created = Student.objects.get_or_create(
                matricule=matricule,
                defaults={
                    "school": school,
                    "prenom": prenom,
                    "postnom": postnom,
                    "nom": nom,
                    "address": address,
                    "latitude": latitude,
                    "longitude": longitude,
                    "parent_phone": parent_phone,
                    "is_active": True,
                    "is_frozen": False,
                    "must_change_password": False,  # démo : accès direct aux interfaces
                },
            )

            if student.parent_phone != parent_phone:
                student.parent_phone = parent_phone
                student.save(update_fields=["parent_phone"])
            if student.school_id != school.id:
                student.school = school
                student.save(update_fields=["school"])
            # Démo : pas de changement de mot de passe imposé (accès direct).
            if student.must_change_password:
                student.must_change_password = False
                student.save(update_fields=["must_change_password"])

            # Compte utilisateur associé : identifiant = matricule, mot de passe = matricule (démo).
            account, account_created = User.objects.get_or_create(username=matricule)
            if account_created:
                account.set_password(matricule)
                account.save()
            if student.user_id != account.id:
                student.user = account
                student.save(update_fields=["user"])
            if created:
                self.stdout.write(
                    self.style.SUCCESS(f"Élève créé : {student}")
                )
            else:
                self.stdout.write(self.style.WARNING(f"Élève déjà présent : {student}"))

        self.stdout.write(
            self.style.SUCCESS(
                f"Seed terminé : {School.objects.count()} école(s), {User.objects.count()} utilisateurs, "
                f"{Bus.objects.count()} bus, {Student.objects.count()} élèves."
            )
        )
