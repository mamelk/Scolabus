import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .models import Bus, BusStop, GPSLog, Incident, Route, RouteStop, School, Student
from .services import auto_assign_student_to_bus, estimate_bus_delay

User = get_user_model()


class AutoAssignTests(TestCase):
    """Affectation automatique des élèves au bus le plus proche (Haversine),
    avec respect de la capacité, alerte de surcapacité et réoptimisation
    dynamique de la feuille de route."""

    def setUp(self):
        # Le moteur central interroge OSRM (géométrie + matrice table) : on
        # neutralise les appels réseau dans les tests (repli sur Haversine /
        # lignes droites).
        self.osrm_patcher = patch(
            "routing.services.get_road_route_geometry", return_value=None
        )
        self.osrm_patcher.start()
        self.table_patcher = patch(
            "routing.services.get_road_distance_matrix", return_value=None
        )
        self.table_patcher.start()
        self.addCleanup(self.osrm_patcher.stop)
        self.addCleanup(self.table_patcher.stop)
        self.school = School.objects.create(
            code_ecole="ECO1", name="École Test", latitude=0.0, longitude=0.0
        )
        # BUS1 : arrêt à ~1,1 km de (0,0) — BUS2 : arrêt à ~11 km de (0,0)
        self.bus1 = self._make_bus("BUS1", capacity=1, stop_lat=0.01, stop_lng=0.0)
        self.bus2 = self._make_bus("BUS2", capacity=2, stop_lat=0.1, stop_lng=0.0)

    def _make_bus(self, code, capacity, stop_lat, stop_lng):
        bus = Bus.objects.create(
            code_bus=code,
            school=self.school,
            capacity=capacity,
            driver_name=f"Chauffeur {code}",
        )
        route = Route.objects.create(name=f"Trajet {code}", bus=bus, school=self.school)
        stop = BusStop.objects.create(name=f"Arrêt {code}", latitude=stop_lat, longitude=stop_lng)
        RouteStop.objects.create(route=route, stop=stop, order=1)
        return bus

    def _student(self, matricule, latitude=None, longitude=None, **kwargs):
        return Student.objects.create(
            matricule=matricule,
            nom="Test",
            address="Adresse",
            school=self.school,
            latitude=latitude,
            longitude=longitude,
            **kwargs,
        )

    def test_creation_with_gps_assigns_closest_bus_automatically(self):
        # Le signal post_save déclenche l'affectation dès la création.
        student = self._student("S1", latitude=0.0, longitude=0.0)
        student.refresh_from_db()
        self.assertEqual(student.assigned_route.bus, self.bus1)
        self.assertFalse(student.overcapacity_alert)

    def test_capacity_full_sends_next_student_to_next_closest_bus(self):
        self._student("S1", latitude=0.0, longitude=0.0)  # occupe BUS1 (capacité 1)
        student2 = self._student("S2", latitude=0.0, longitude=0.0)
        student2.refresh_from_db()
        self.assertEqual(student2.assigned_route.bus, self.bus2)

    def test_all_buses_full_marks_overcapacity_alert(self):
        # BUS1 (cap 1) + BUS2 (cap 2) = 3 places au total.
        for i in range(1, 4):
            self._student(f"S{i}", latitude=0.0, longitude=0.0)
        over = self._student("S4", latitude=0.0, longitude=0.0)
        over.refresh_from_db()
        self.assertIsNone(over.assigned_route)
        self.assertTrue(over.overcapacity_alert)

    def test_student_without_gps_stays_unassigned(self):
        student = self._student("S1")
        self.assertIsNone(student.assigned_route)
        self.assertFalse(student.overcapacity_alert)

    def test_function_reassigns_after_capacity_frees_up(self):
        # 4 élèves pour 3 places : le dernier créé est en surcapacité.
        for i in range(1, 4):
            self._student(f"S{i}", latitude=0.0, longitude=0.0)
        over = self._student("S4", latitude=0.0, longitude=0.0)
        over.refresh_from_db()
        self.assertTrue(over.overcapacity_alert)
        # Un élève quitte BUS2 : la place se libère → nouvelle affectation possible.
        freed = Student.objects.get(matricule="S2")
        freed.assigned_route = None
        freed.save(update_fields=["assigned_route"])
        bus = auto_assign_student_to_bus(over)
        over.refresh_from_db()
        self.assertIsNotNone(bus)
        self.assertFalse(over.overcapacity_alert)
        self.assertIsNotNone(over.assigned_route)

    def test_parent_onboarding_assigns_and_displays_bus_and_driver(self):
        parent = User.objects.create_user(username="PARENT", password="x")
        student = self._student("S1", parent_user=parent)
        self.client.force_login(parent)
        resp = self.client.post(
            "/parent/setup-home/",
            {"latitude": 0.0, "longitude": 0.0},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        student.refresh_from_db()
        self.assertEqual(student.assigned_route.bus, self.bus1)
        # Le parent voit immédiatement le bus et le chauffeur attribués.
        content = resp.content.decode()
        self.assertIn("BUS1", content)
        self.assertIn("Chauffeur BUS1", content)

    def test_adding_bus_auto_assigns_pending_students(self):
        school2 = School.objects.create(
            code_ecole="ECO4", name="École 4", latitude=0.0, longitude=0.0
        )
        admin = User.objects.create_user(username="ADMIN4", password="x")
        school2.user = admin
        school2.save(update_fields=["user"])
        # Créés avant l'existence des bus : ils restent en surcapacité.
        s1 = Student.objects.create(
            matricule="S1", nom="Test", address="A", school=school2,
            latitude=0.0, longitude=0.0,
        )
        s2 = Student.objects.create(
            matricule="S2", nom="Test", address="A", school=school2,
            latitude=0.01, longitude=0.0,
        )
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertTrue(s1.overcapacity_alert)
        self.assertTrue(s2.overcapacity_alert)
        # L'ajout d'un bus déclenche automatiquement le moteur central :
        # plus aucun bouton manuel n'est nécessaire.
        Bus.objects.create(
            code_bus="B1", school=school2, capacity=5, driver_name="Chauffeur B1"
        )
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertIsNotNone(s1.assigned_route)
        self.assertIsNotNone(s2.assigned_route)
        self.assertFalse(s1.overcapacity_alert)
        self.assertFalse(s2.overcapacity_alert)

    def test_onboarding_reoptimizes_route_and_driver_api_reflects_it(self):
        # BUS1 (cap 1) est rempli par S1 ; S2 va sur BUS2.
        s1 = self._student("S1", latitude=0.001, longitude=0.0)
        s2 = self._student("S2", latitude=0.002, longitude=0.0)
        s1.refresh_from_db()
        s2.refresh_from_db()
        self.assertEqual(s1.assigned_route.bus, self.bus1)
        self.assertEqual(s2.assigned_route.bus, self.bus2)
        # Un nouveau parent valide son domicile → affectation + réoptimisation.
        parent = User.objects.create_user(username="PARENT2", password="x")
        s3 = self._student("S3", parent_user=parent)
        self.client.force_login(parent)
        resp = self.client.post(
            "/parent/setup-home/",
            {"latitude": 0.003, "longitude": 0.0},
            follow=True,
        )
        self.assertEqual(resp.status_code, 200)
        s3.refresh_from_db()
        self.assertIsNotNone(s3.assigned_route)
        # BUS1 est plein : S3 rejoint BUS2.
        self.assertEqual(s3.assigned_route.bus, self.bus2)
        # Feuille de route réoptimisée : 2 arrêts (S2 puis S3), ordre optimal.
        route = self.bus2.routes.order_by("-created_at").first()
        stops = list(route.stops.order_by("order"))
        self.assertEqual(len(stops), 2)
        self.assertEqual(route.students_remaining, 2)
        self.assertIsNotNone(route.path_geometry)  # repli lignes droites (OSRM neutralisé)
        # L'API chauffeur reflète le nouvel itinéraire dès le prochain rafraîchissement.
        driver_user = User.objects.create_user(username="DRV2", password="x")
        self.bus2.driver_user = driver_user
        self.bus2.save(update_fields=["driver_user"])
        self.client.force_login(driver_user)
        resp = self.client.get("/api/driver/route/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual({s["id"] for s in data["students"]}, {s2.id, s3.id})
        self.assertEqual(data["total"], 2)

    def test_dashboard_shows_alert_when_students_unassigned(self):
        school2 = School.objects.create(
            code_ecole="ECO2", name="École 2", latitude=0.0, longitude=0.0
        )
        admin = User.objects.create_user(username="ADMIN2", password="x")
        school2.user = admin
        school2.save(update_fields=["user"])
        # Aucun bus dans cette école : l'élève avec GPS reste non affecté.
        Student.objects.create(
            matricule="S1",
            nom="Test",
            address="Adresse",
            school=school2,
            latitude=0.0,
            longitude=0.0,
        )
        # Élève sans GPS : non compté dans la bannière.
        Student.objects.create(
            matricule="S2", nom="Test", address="Adresse", school=school2
        )
        self.client.force_login(admin)
        resp = self.client.get("/dashboard/")
        self.assertEqual(resp.status_code, 200)
        content = resp.content.decode()
        self.assertIn("1 élève(s) non affecté(s) au transport", content)
        # Le bouton manuel d'affectation a disparu : tout est automatique.
        self.assertNotIn("Lancer l'affectation automatique globale", content)
        self.assertNotIn("Générer / Optimiser le trajet", content)

    def test_route_sheet_blocked_while_students_unassigned(self):
        school2 = School.objects.create(
            code_ecole="ECO3", name="École 3", latitude=0.0, longitude=0.0
        )
        admin = User.objects.create_user(username="ADMIN3", password="x")
        school2.user = admin
        school2.save(update_fields=["user"])
        bus = Bus.objects.create(code_bus="B1", school=school2, capacity=1)
        # Deux élèves avec GPS pour une seule place : le second reste non affecté.
        Student.objects.create(
            matricule="S1", nom="Test", address="Adresse",
            school=school2, latitude=0.0, longitude=0.0,
        )
        Student.objects.create(
            matricule="S2", nom="Test", address="Adresse",
            school=school2, latitude=0.01, longitude=0.0,
        )
        self.client.force_login(admin)
        # Bloqué : un élève avec GPS n'est pas affecté (capacité insuffisante).
        resp = self.client.get(f"/export/feuille-route/{bus.pk}/")
        self.assertEqual(resp.status_code, 302)
        # La capacité augmente → recalcul automatique → feuille de route générée.
        bus.capacity = 5
        bus.save(update_fields=["capacity"])
        resp = self.client.get(f"/export/feuille-route/{bus.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "application/pdf")


class SyncOfflineDataTests(TestCase):
    """Endpoint /api/sync-offline-data/ : resynchronisation groupée des données
    accumulées par le téléphone du chauffeur pendant une coupure réseau."""

    def setUp(self):
        self.school = School.objects.create(
            code_ecole="ECOLE1",
            name="École Test",
            latitude=-11.6647,
            longitude=27.4794,
        )
        self.driver_user = User.objects.create_user(username="BUS001", password="bus001")
        self.bus = Bus.objects.create(
            code_bus="BUS001",
            school=self.school,
            capacity=30,
            driver_name="Chauffeur Test",
            driver_user=self.driver_user,
        )
        self.client.force_login(self.driver_user)

    def _post(self, payload):
        return self.client.post(
            "/api/sync-offline-data/",
            data=json.dumps(payload),
            content_type="application/json",
        )

    def test_inserts_points_and_incidents_in_bulk(self):
        base = timezone.now()
        ts1 = base.isoformat()
        ts2 = (base + timedelta(seconds=1)).isoformat()
        resp = self._post(
            {
                "points": [
                    {"latitude": -11.6647, "longitude": 27.4794, "speed": 12.5, "timestamp": ts1},
                    {"latitude": -11.6650, "longitude": 27.4800, "speed": 0, "timestamp": ts2},
                ],
                "incidents": [
                    {"type_incident": "Panne", "description": "Pneu crevé", "timestamp": ts1},
                ],
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["synced_points"], 2)
        self.assertEqual(data["synced_incidents"], 1)
        self.assertEqual(GPSLog.objects.filter(bus=self.bus).count(), 2)
        self.assertEqual(
            Incident.objects.filter(bus=self.bus, type_incident="Panne").count(), 1
        )
        # Dernier état connu du bus mis à jour (point le plus récent du lot).
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.last_latitude, -11.6650)
        self.assertEqual(self.bus.last_longitude, 27.4800)
        self.assertTrue(self.bus.driver_connected)

    def test_older_points_do_not_overwrite_newer_state(self):
        older = (timezone.now() - timedelta(minutes=30)).isoformat()
        self.bus.last_latitude = 1.0
        self.bus.last_longitude = 2.0
        self.bus.last_position_at = timezone.now()
        self.bus.save()
        resp = self._post(
            {
                "points": [
                    {"latitude": -11.1, "longitude": 27.1, "speed": 0, "timestamp": older},
                ]
            }
        )
        self.assertEqual(resp.status_code, 200)
        self.bus.refresh_from_db()
        # La position plus récente déjà en base n'est pas écrasée.
        self.assertEqual(self.bus.last_latitude, 1.0)
        self.assertEqual(self.bus.last_longitude, 2.0)

    def test_invalid_points_are_skipped_without_failing_batch(self):
        ts = timezone.now().isoformat()
        resp = self._post(
            {
                "points": [
                    {"latitude": "invalide", "longitude": 0, "speed": 0, "timestamp": ts},
                    {"latitude": 999, "longitude": 0, "speed": 0, "timestamp": ts},  # hors limites
                    {"latitude": -11.66, "longitude": 27.48, "speed": -5, "timestamp": ts},
                ],
                "incidents": [
                    {"type_incident": "TypeInconnu", "description": "", "timestamp": ts},
                ],
            }
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["synced_points"], 1)
        self.assertEqual(data["synced_incidents"], 0)
        log = GPSLog.objects.get(bus=self.bus)
        self.assertEqual(log.latitude, -11.66)
        self.assertEqual(log.speed_kmh, 0.0)  # vitesse négative ramenée à 0

    def test_non_driver_is_rejected(self):
        other = User.objects.create_user(username="AUTRE", password="x")
        self.client.force_login(other)
        resp = self._post({"points": [{"latitude": 0, "longitude": 0, "speed": 0}]})
        self.assertEqual(resp.status_code, 403)

    def test_invalid_payload_is_rejected(self):
        resp = self.client.post(
            "/api/sync-offline-data/",
            data="pas du json",
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        resp = self._post({"points": "nope"})
        self.assertEqual(resp.status_code, 400)

    def test_get_method_is_rejected(self):
        resp = self.client.get("/api/sync-offline-data/")
        self.assertEqual(resp.status_code, 405)


class RealtimeApiTests(TestCase):
    """Endpoints de localisation temps réel : /api/bus/<id>/location/ et
    /api/school/fleet/ (position, feuille de route, statut, compteurs)."""

    def setUp(self):
        self.school = School.objects.create(
            code_ecole="ECOAPI", name="École API", latitude=0.0, longitude=0.0
        )
        self.bus = Bus.objects.create(
            code_bus="BUSX",
            school=self.school,
            capacity=10,
            driver_name="Chauffeur X",
            last_latitude=-11.66,
            last_longitude=27.47,
            last_position_at=timezone.now(),
            driver_connected=True,
        )
        # L'élève (avec GPS) est affecté automatiquement au bus : feuille de route créée.
        self.student = Student.objects.create(
            matricule="SA1", nom="Test", address="A", school=self.school,
            latitude=0.001, longitude=0.0,
        )
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.assigned_route)

    def test_parent_sees_bus_location_route_and_status(self):
        parent = User.objects.create_user(username="PARENT_API", password="x")
        self.student.parent_user = parent
        self.student.save(update_fields=["parent_user"])
        self.client.force_login(parent)
        resp = self.client.get(f"/api/bus/{self.bus.pk}/location/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["code_bus"], "BUSX")
        self.assertEqual(data["status"], "en_route")
        self.assertIsNotNone(data["latitude"])
        self.assertIsNotNone(data["last_updated"])
        self.assertTrue(data["is_active"])
        self.assertEqual(data["route"]["students_remaining"], 1)
        self.assertEqual(len(data["route"]["stops"]), 1)

    def test_fleet_api_returns_buses_and_counters(self):
        admin = User.objects.create_user(username="ADMIN_API", password="x")
        self.school.user = admin
        self.school.save(update_fields=["user"])
        self.client.force_login(admin)
        resp = self.client.get("/api/school/fleet/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["buses"]), 1)
        self.assertEqual(data["buses"][0]["code_bus"], "BUSX")
        self.assertEqual(data["buses"][0]["latitude"], -11.66)
        self.assertGreaterEqual(data["counters"]["assigned"], 1)
        self.assertIn("incidents", data["counters"])

    def test_bus_location_denied_for_stranger(self):
        stranger = User.objects.create_user(username="STRANGER", password="x")
        self.client.force_login(stranger)
        resp = self.client.get(f"/api/bus/{self.bus.pk}/location/")
        self.assertEqual(resp.status_code, 403)

    def test_fleet_api_reports_speed_and_speeding_flag(self):
        admin = User.objects.create_user(username="ADMIN_SP", password="x")
        self.school.user = admin
        self.school.save(update_fields=["user"])
        self.bus.speed_kmh = 62.0
        self.bus.save(update_fields=["speed_kmh"])
        self.client.force_login(admin)
        resp = self.client.get("/api/school/fleet/")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["buses"][0]["speed_kmh"], 62.0)
        self.assertTrue(data["buses"][0]["is_speeding"])
        self.assertEqual(data["counters"]["speed_alerts"], 1)

    def test_driver_position_stores_measured_speed(self):
        # Le chauffeur envoie sa position avec la vitesse mesurée par l'appareil.
        self.bus.driver_user = User.objects.create_user(username="DRVSPD", password="x")
        self.bus.save(update_fields=["driver_user"])
        self.client.force_login(self.bus.driver_user)
        resp = self.client.post(
            "/driver/position/",
            {"latitude": -11.66, "longitude": 27.47, "speed": 12.5},
        )
        self.assertEqual(resp.status_code, 200)
        self.bus.refresh_from_db()
        self.assertEqual(self.bus.speed_kmh, 12.5)
        self.assertFalse(self.bus.speed_kmh > 50)

    def test_full_realtime_cycle_driver_parent_school(self):
        """Cycle complet : affectation automatique → position + vitesse du
        chauffeur → suivi temps réel parent (vitesse, ETA, excès) et école
        (flotte + alerte d'excès de vitesse)."""
        # Le parent est lié à l'élève (déjà affecté automatiquement au bus).
        parent = User.objects.create_user(username="CYCLE_PARENT", password="x")
        self.student.parent_user = parent
        self.student.save(update_fields=["parent_user"])
        # Le chauffeur envoie sa position (hors dépôt) et sa vitesse mesurée.
        self.bus.driver_user = User.objects.create_user(username="CYCLE_DRV", password="x")
        self.bus.save(update_fields=["driver_user"])
        self.client.force_login(self.bus.driver_user)
        resp = self.client.post(
            "/driver/position/",
            {"latitude": 0.01, "longitude": 0.0, "speed": 58.0},
        )
        self.assertEqual(resp.status_code, 200)
        # Le parent voit la position, la vitesse, l'ETA et l'alerte d'excès.
        self.client.force_login(parent)
        data = self.client.get("/api/parent/live/").json()
        self.assertEqual(data["status"], "en_route")
        self.assertEqual(data["speed_kmh"], 58.0)
        self.assertTrue(data["is_speeding"])
        self.assertIsNotNone(data["eta_minutes"])
        # L'école voit l'excès de vitesse dans la flotte.
        admin = User.objects.create_user(username="CYCLE_ADMIN", password="x")
        self.school.user = admin
        self.school.save(update_fields=["user"])
        self.client.force_login(admin)
        fleet = self.client.get("/api/school/fleet/").json()
        self.assertTrue(any(b["is_speeding"] for b in fleet["buses"]))
        self.assertGreaterEqual(fleet["counters"]["speed_alerts"], 1)


class BusDelayTests(TestCase):
    """Estimation du retard réel : départ effectif (premier GPS du jour à plus
    de 400 m de l'école) vs progression réelle le long de la géométrie du
    trajet (horaire prévu reconstruit depuis la durée estimée)."""

    def setUp(self):
        self.school = School.objects.create(
            code_ecole="ECODLY", name="École Retard", latitude=0.0, longitude=0.0
        )
        self.bus = Bus.objects.create(
            code_bus="BUSDLY", school=self.school, capacity=10, driver_name="Chauffeur D"
        )
        self.route = Route.objects.create(
            name="Trajet test",
            bus=self.bus,
            school=self.school,
            estimated_duration_minutes=60.0,
            path_geometry=[
                [0.0, 0.0], [0.0, 0.1], [0.0, 0.2], [0.0, 0.3], [0.0, 0.4], [0.0, 0.5],
            ],
        )
        self.now = timezone.localtime()
        # Départ effectif : premier GPS du jour à plus de 400 m de l'école.
        dep = GPSLog.objects.create(bus=self.bus, latitude=0.0, longitude=0.01)
        GPSLog.objects.filter(pk=dep.pk).update(
            timestamp=self.now - timedelta(minutes=10)
        )

    def _position(self, longitude):
        self.bus.last_latitude = 0.0
        self.bus.last_longitude = longitude
        self.bus.last_position_at = self.now
        self.bus.save(
            update_fields=["last_latitude", "last_longitude", "last_position_at"]
        )

    def test_bus_behind_schedule_is_late(self):
        # 10 % de la boucle parcourue après 10 min sur 60 min prévues → retard.
        self._position(0.05)
        delay = estimate_bus_delay(self.bus, self.route, now=self.now)
        self.assertIsNotNone(delay)
        self.assertGreater(delay, 0)
        self.assertAlmostEqual(delay, 4.0, delta=1.0)

    def test_bus_ahead_of_schedule_is_early(self):
        # 70 % de la boucle parcourue après 10 min → en avance.
        self._position(0.35)
        delay = estimate_bus_delay(self.bus, self.route, now=self.now)
        self.assertIsNotNone(delay)
        self.assertLess(delay, 0)

    def test_bus_without_recent_gps_has_no_delay(self):
        self.bus.last_latitude = 0.0
        self.bus.last_longitude = 0.05
        self.bus.last_position_at = None
        self.assertIsNone(estimate_bus_delay(self.bus, self.route, now=self.now))

    def test_bus_still_at_depot_has_no_delay(self):
        # Tous les GPS du jour restent près de l'école : aucun départ détecté.
        GPSLog.objects.filter(bus=self.bus).update(latitude=0.0, longitude=0.001)
        self._position(0.001)
        self.assertIsNone(estimate_bus_delay(self.bus, self.route, now=self.now))


class ServiceWorkerTests(TestCase):
    """Le Service Worker est servi à la racine avec la portée couvrant le site."""

    def test_sw_served_at_root_with_scope_header(self):
        resp = self.client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Service-Worker-Allowed"], "/")
        self.assertIn("application/javascript", resp["Content-Type"])
        self.assertIn(b"tile.openstreetmap.org", resp.content)
