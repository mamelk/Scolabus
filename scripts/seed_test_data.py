"""Seed de données réalistes pour tester le clustering spatial des élèves."""
import os
import random

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scolaloop.settings")

import django
django.setup()

from django.contrib.auth.models import User
from routing.models import Bus, Route, RouteStop, BusStop, School, Student

school = School.objects.get(code_ecole="ECO")
lat0, lon0 = school.latitude, school.longitude
print(f"École : {school.name} ({lat0}, {lon0})")

# ── 1) Bus ──────────────────────────────────────────────────
bus_data = [
    ("BUS",    20, "Kasongo"),
    ("BUS-02", 18, "Mutombo"),
    ("BUS-03", 15, "Tshimanga"),
    ("BUS-04", 20, "Kalubi"),
]
buses = []
for code, cap, driver in bus_data:
    bus, created = Bus.objects.get_or_create(
        code_bus=code, school=school,
        defaults={"capacity": cap, "driver_name": driver},
    )
    buses.append(bus)
    if created:
        user, uc = User.objects.get_or_create(username=code)
        if uc:
            user.set_password(code)
            user.save()
        bus.driver_user = user
        bus.save(update_fields=["driver_user"])
    print(f"  Bus {bus.code_bus} — {bus.capacity} places — {bus.driver_name}")

# ── 2) Nettoyer les anciens élèves ─────────────────────────
Student.objects.filter(school=school).delete()
print(f"\nAnciens élèves supprimés.")

# ── 3) Quartiers de Lubumbashi (offsets en degrés) ──────────
quartiers = {
    "Kampemba":     (lat0 - 0.015, lon0 + 0.010, 0.005),
    "Kasapa":       (lat0 + 0.012, lon0 - 0.008, 0.004),
    "Universite":   (lat0 + 0.008, lon0 + 0.015, 0.003),
    "Centre-ville": (lat0 - 0.003, lon0 - 0.012, 0.002),
    "Lubumbashi":   (lat0 + 0.020, lon0 + 0.005, 0.006),
    "Mikalishi":    (lat0 - 0.025, lon0 + 0.020, 0.005),
    "Bongonga":     (lat0 + 0.005, lon0 - 0.020, 0.004),
    "Kasenga":      (lat0 - 0.010, lon0 - 0.015, 0.003),
}

prenoms = [
    "Jean", "Marie", "Pierre", "Grace", "David", "Sarah", "Paul", "Ruth",
    "Joseph", "Annie", "Emmanuel", "Blessing", "Daniel", "Catherine", "Samuel",
    "Esther", "Patrick", "Josiane", "Michael", "Judith", "Olivier", "Monique",
    "Dieudonne", "Esperance", "Felicien", "Arlette", "Blaise", "Chantal",
    "Clement", "Francoise", "Gerard", "Helene", "Irving", "Josephine",
    "Kevin", "Leonie", "Maxime", "Nadege", "Patrice", "Prisca",
]
noms = [
    "Mukendi", "Kalala", "Tshimanga", "Mutombo", "Kasongo", "Lubaba",
    "Ilunga", "Kabongo", "Mwamba", "Ngalula", "Kasumba", "Bukasa",
    "Tshilombo", "Lukusa", "Mwenze", "Ngandu", "Kapenda", "Lubobo",
    "Musonda", "Shula", "Mbuyi", "Kiese", "Banze", "Kadima",
    "Kyungu", "Tshala", "Ndaye", "Likulia", "Sampassa", "Mulenda",
]

students = []
idx = 1
for quartier, (b_lat, b_lon, spread) in quartiers.items():
    n = random.randint(4, 7)
    for _ in range(n):
        lat = b_lat + random.uniform(-spread, spread)
        lon = b_lon + random.uniform(-spread, spread)
        s = Student.objects.create(
            matricule=f"MAT-{idx:04d}",
            school=school,
            prenom=prenoms[idx % len(prenoms)],
            postnom=quartier,
            nom=noms[idx % len(noms)],
            address=f"Quartier {quartier}, Lubumbashi",
            latitude=lat,
            longitude=lon,
            is_active=True,
            is_frozen=False,
            must_change_password=True,
            parent_phone=f"+243{random.randint(800000000, 899999999)}",
        )
        students.append(s)
        idx += 1

print(f"\n{len(students)} élèves créés dans {len(quartiers)} quartiers\n")

from collections import Counter
qc = Counter(s.postnom for s in students)
for q, c in sorted(qc.items(), key=lambda x: -x[1]):
    print(f"  {q:15s} : {c} élèves")

print(f"\nTotal élèves : {Student.objects.filter(school=school).count()}")
print(f"Total bus    : {Bus.objects.filter(school=school).count()}")
