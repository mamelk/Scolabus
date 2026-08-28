"""Service d'optimisation de tournées de bus scolaires.

Utilise OR-Tools (Google) pour résoudre un problème de tournées de
véhicules (VRP) avec contraintes de capacité : chaque bus = un véhicule,
chaque élève = une demande de 1 place.

Chaque trajet forme une boucle fermée :
    École (départ) → élèves actifs et non gelés → École (retour obligatoire).
"""

import logging
import math

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import os

from .models import Absence, Bus, BusStop, GPSLog, Route, RouteStop, School, Student

EARTH_RADIUS_KM = 6371.0088
AVERAGE_SPEED_KMH = 30.0  # vitesse moyenne de circulation (km/h)
logger = logging.getLogger(__name__)

# Rayon de regroupement des élèves en arrêts communs (mètres).
# Deux élèves domiciliés à moins de CLUSTER_RADIUS m l'un de l'autre
# partagent le même arrêt de bus (le chauffeur ne s'arrête qu'une fois).
CLUSTER_RADIUS_METERS = int(os.environ.get('CLUSTER_RADIUS_METERS', '200'))


# Session HTTP réutilisée avec nouvelles tentatives (le serveur public OSRM
# est lent et instable) : 3 essais avec backoff sur erreurs réseau/5xx.
_OSRM_RETRIES = Retry(
    total=3,
    connect=3,
    read=3,
    backoff_factor=0.5,
    status_forcelist=[429, 500, 502, 503, 504],
)
_osrm_session = requests.Session()
_osrm_session.mount("http://", HTTPAdapter(max_retries=_OSRM_RETRIES))
_osrm_session.mount("https://", HTTPAdapter(max_retries=_OSRM_RETRIES))


def _osrm_base_url():
    return getattr(settings, "OSRM_BASE_URL", "http://router.project-osrm.org/route/v1/driving")


def _osrm_timeout():
    return getattr(settings, "OSRM_TIMEOUT_SECONDS", 20)


def absent_student_ids(target_date=None):
    """Ids des élèves déclarés absents à la date donnée (défaut : aujourd'hui,
    dans le fuseau horaire de l'application — cohérent avec les vues)."""
    if target_date is None:
        target_date = timezone.localdate()
    return set(Absence.objects.filter(date=target_date).values_list("student_id", flat=True))


def haversine_km(lat1, lon1, lat2, lon2):
    """Distance orthodromique entre deux points GPS, en kilomètres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- Regroupement spatial des élèves


def _haversine_m(lat1, lon1, lat2, lon2):
    """Distance orthodromique entre deux points GPS, en mètres."""
    return haversine_km(lat1, lon1, lat2, lon2) * 1000


def cluster_students_by_proximity(students, radius_meters=None):
    """Regroupe les élèves par proximité géographique en arrêts communs.

    Algorithme glouton (greedy clustering) :
    1. Pour chaque élève non encore groupé, on cherche tous les voisins
       dans un rayon de `radius_meters` mètres.
    2. Un arrêt commun (BusStop) est créé au centroïde du groupe.
    3. Tous les élèves du groupe partagent le même arrêt.

    Retourne une liste de clusters, chacun étant un dict :
        {
            'centroid': (latitude, longitude),
            'students': [Student, ...],
            'radius_m': float,  # rayon effectif du groupe (distance max au centroïde)
        }
    """
    if radius_meters is None:
        radius_meters = CLUSTER_RADIUS_METERS
    radius_km = radius_meters / 1000.0

    remaining = list(students)
    clusters = []

    while remaining:
        # Prendre le premier élève restant comme graine du cluster
        seed = remaining[0]
        group = [seed]
        seed_lat, seed_lon = seed.latitude, seed.longitude

        # Trouver tous les voisins dans le rayon
        neighbors = []
        for s in remaining[1:]:
            dist = _haversine_m(seed_lat, seed_lon, s.latitude, s.longitude)
            if dist <= radius_meters:
                neighbors.append((dist, s))

        # Ajouter les voisins au groupe (triés par distance croissante)
        neighbors.sort(key=lambda x: x[0])
        for dist, s in neighbors:
            # Vérifier aussi la distance au centroïde du groupe en cours
            group_lat = sum(st.latitude for st in group) / len(group)
            group_lon = sum(st.longitude for st in group) / len(group)
            if _haversine_m(group_lat, group_lon, s.latitude, s.longitude) <= radius_meters:
                group.append(s)

        # Retirer tous les élèves du groupe de la liste restante
        group_ids = {s.pk for s in group}
        remaining = [s for s in remaining if s.pk not in group_ids]

        # Calculer le centroïde du groupe
        centroid_lat = sum(s.latitude for s in group) / len(group)
        centroid_lon = sum(s.longitude for s in group) / len(group)

        # Rayon effectif : distance max entre le centroïde et un membre du groupe
        max_radius = max(
            _haversine_m(centroid_lat, centroid_lon, s.latitude, s.longitude)
            for s in group
        ) if len(group) > 1 else 0.0

        clusters.append({
            'centroid': (centroid_lat, centroid_lon),
            'students': group,
            'radius_m': round(max_radius, 1),
        })

    return clusters


# ---------------------------------------------------------------- Affectation automatique des élèves


def _student_to_route_distance(student, route):
    """Distance minimale (km, Haversine) entre le domicile de l'élève et les
    points de passage habituels du trajet : arrêts (BusStop) puis géométrie
    routière (path_geometry). Retourne None si le trajet n'a aucun point connu."""
    if route is None:
        return None
    candidates = []
    for route_stop in route.stops.select_related("stop").all():
        stop = route_stop.stop
        candidates.append((stop.latitude, stop.longitude))
    for point in route.path_geometry or []:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                candidates.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                continue
    if not candidates:
        return None
    return min(
        haversine_km(student.latitude, student.longitude, lat, lon)
        for lat, lon in candidates
    )


def _bus_assigned_count(bus, exclude_student=None):
    """Nombre d'élèves actuellement affectés aux trajets du bus (l'élève en
    cours de traitement est exclu : la fonction doit être idempotente)."""
    qs = Student.objects.filter(assigned_route__bus=bus)
    if exclude_student is not None:
        qs = qs.exclude(pk=exclude_student.pk)
    return qs.count()


def auto_assign_student_to_bus(student):
    """Affecte automatiquement un élève au bus actif le plus proche de son domicile.

    Règles :
    - Sans coordonnées GPS → l'élève reste en statut « non affecté » (unassigned).
    - La distance (Haversine) est calculée entre le domicile de l'élève et les
      points de passage habituels (arrêts + géométrie) du trajet de chaque bus.
    - Le bus retenu est le plus proche dont la capacité (capacity) n'est pas
      encore atteinte ; l'affectation est enregistrée sur le trajet le plus
      récent de ce bus (assigned_route).
    - Si aucun bus proche n'a de place → overcapacity_alert = True (alerte admin).

    Retourne le Bus affecté, ou None.
    """
    if student.latitude is None or student.longitude is None:
        # Pas de GPS : reste non affecté (l'école ou le parent devra définir la maison).
        if student.assigned_route_id is not None or student.overcapacity_alert:
            student.assigned_route = None
            student.overcapacity_alert = False
            student.save(update_fields=["assigned_route", "overcapacity_alert"])
        return None

    school = student.school
    if school is None:
        return None

    buses = Bus.objects.filter(school=school, is_in_service=True).order_by("code_bus")
    best = None  # (distance_km, bus, route)
    for bus in buses:
        if _bus_assigned_count(bus, exclude_student=student) >= bus.capacity:
            continue  # bus complet : capacité maximale atteinte
        route = bus.routes.order_by("-created_at").first()
        distance = _student_to_route_distance(student, route)
        if distance is not None and (best is None or distance < best[0]):
            best = (distance, bus, route)

    if best is None:
        # Aucun bus actif avec de la place (ou aucun trajet défini) : alerte admin.
        changed = student.assigned_route_id is not None or not student.overcapacity_alert
        student.assigned_route = None
        student.overcapacity_alert = True
        if changed:
            student.save(update_fields=["assigned_route", "overcapacity_alert"])
        return None

    _, bus, route = best
    changed = student.assigned_route_id != route.id or student.overcapacity_alert
    student.assigned_route = route
    student.overcapacity_alert = False
    if changed:
        student.save(update_fields=["assigned_route", "overcapacity_alert"])
    return bus


# ---------------------------------------------------------------- Moteur central (100 % événementiel)


def _eligible_students(school):
    """Élèves éligibles à la tournée : actifs (non gelés), avec coordonnées GPS
    (domicile défini par le parent) et non absents aujourd'hui."""
    students = list(
        Student.objects.filter(
            school=school,
            is_active=True,
            is_frozen=False,
            latitude__isnull=False,
            longitude__isnull=False,
        ).order_by("nom", "prenom")
    )
    absent = absent_student_ids()
    return [s for s in students if s.id not in absent]


def _build_route(school, bus, ordered, route=None):
    """Crée (route=None) ou met à jour (route donnée) la feuille de route du
    bus pour les élèves donnés, dans l'ordre optimal de passage, avec ses
    arrêts (BusStop + RouteStop) et le suivi de ramassage préservé
    (is_taken / parent_notified restent inchangés : un recalcul en cours de
    journée ne perd jamais la progression du chauffeur).

    Les élèves proches géographiquement (dans un rayon de CLUSTER_RADIUS_METERS)
    sont regroupés en un seul arrêt commun : le chauffeur ne s'arrête qu'une
    fois pour ramasser tous les élèves du groupe.

    Retourne l'instance Route.
    """
    depot = (school.latitude, school.longitude)

    # --- Regroupement spatial : un arrêt par zone, pas un arrêt par élève ---
    clusters = cluster_students_by_proximity(ordered)
    # Tri des clusters par distance au dépôt (ordre optimal de passage)
    clusters.sort(key=lambda c: haversine_km(depot[0], depot[1], c['centroid'][0], c['centroid'][1]))

    # Points de passage : dépôt → centroïdes des clusters → dépôt
    ordered_points = [depot] + [c['centroid'] for c in clusters] + [depot]
    total_km = sum(
        haversine_km(*ordered_points[i], *ordered_points[i + 1])
        for i in range(len(ordered_points) - 1)
    )
    if route is None:
        route = Route.objects.create(
            name=f"Route {bus.code_bus} — {timezone.localdate().isoformat()}",
            bus=bus,
            school=school,
            total_distance_km=round(total_km, 2),
            estimated_duration_minutes=round(total_km / AVERAGE_SPEED_KMH * 60, 1),
            students_taken=sum(1 for s in ordered if s.is_taken),
            students_remaining=sum(1 for s in ordered if not s.is_taken),
        )
    else:
        route.stops.all().delete()  # réordonne les arrêts existants
        route.total_distance_km = round(total_km, 2)
        route.estimated_duration_minutes = round(total_km / AVERAGE_SPEED_KMH * 60, 1)
        route.students_taken = sum(1 for s in ordered if s.is_taken)
        route.students_remaining = sum(1 for s in ordered if not s.is_taken)
        route.save(
            update_fields=[
                "total_distance_km",
                "estimated_duration_minutes",
                "students_taken",
                "students_remaining",
            ]
        )

    # --- Création des arrêts groupés ---
    all_students_update = []
    for order, cluster in enumerate(clusters, start=1):
        centroid_lat, centroid_lon = cluster['centroid']
        n_students = len(cluster['students'])
        # Nom de l'arrêt : "Arrêt Zone 1 (3 élèves)" ou "Arrêt Dupont (1 élève)"
        if n_students > 1:
            stop_name = f"Arrêt Zone {order} ({n_students} élèves)"
        else:
            s = cluster['students'][0]
            stop_name = f"Arrêt {s.prenom} {s.nom}"

        stop, _ = BusStop.objects.get_or_create(
            name=stop_name,
            defaults={
                "latitude": centroid_lat,
                "longitude": centroid_lon,
            },
        )
        # Synchroniser les coordonnées si le centroïde a changé
        if stop.latitude != centroid_lat or stop.longitude != centroid_lon:
            stop.latitude = centroid_lat
            stop.longitude = centroid_lon
            stop.save(update_fields=["latitude", "longitude"])
        RouteStop.objects.create(route=route, stop=stop, order=order)

        # Tous les élèves du cluster sont rattachés au trajet
        for student in cluster['students']:
            student.assigned_route = route
            student.overcapacity_alert = False
            all_students_update.append(student)

    Student.objects.bulk_update(all_students_update, ["assigned_route", "overcapacity_alert"])
    return route


def _attach_route_geometry(route, timeout=None):
    """Interroge OSRM pour la géométrie routière réelle de la boucle
    École → arrêts → École ; repli sur les lignes droites si le service est
    injoignable (la carte du chauffeur reste lisible immédiatement)."""
    if route.school is None:
        return
    waypoints = [(route.school.latitude, route.school.longitude)]
    waypoints += [
        (rs.stop.latitude, rs.stop.longitude) for rs in route.stops.order_by("order")
    ]
    waypoints.append((route.school.latitude, route.school.longitude))
    road = get_road_route_geometry(waypoints, timeout=timeout)
    if road:
        route.path_geometry = road["geometry"]
        route.total_distance_km = road["distance_km"]
        route.estimated_duration_minutes = road["duration_minutes"]
        route.save(
            update_fields=[
                "path_geometry",
                "total_distance_km",
                "estimated_duration_minutes",
            ]
        )
    else:
        route.path_geometry = [[lat, lon] for lat, lon in waypoints]
        route.save(update_fields=["path_geometry"])


# ---------------------------------------------------------------- Estimation du retard réel

# Seuil de sortie du dépôt : au-delà de 400 m de l'école, le bus est parti.
_DEPARTURE_DISTANCE_KM = 0.4
# Un bus est considéré « en retard » si l'écart dépasse 5 minutes.
DELAY_THRESHOLD_MINUTES = 5.0
# Limite de vitesse (km/h) au-delà de laquelle le bus est en excès (alerte).
SPEED_LIMIT_KMH = 50.0


def _project_on_segment(p, a, b):
    """Projection approximée de p sur le segment [a, b] (plan lat/lon).
    Retourne (t, d) : paramètre 0..1 de la projection et distance (km) de
    p au segment."""
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    px, py = p[0], p[1]
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return 0.0, haversine_km(ax, ay, px, py)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = ax + t * dx, ay + t * dy
    return t, haversine_km(px, py, qx, qy)


def _distance_along_route(points, target):
    """Progression le long de la polyligne : (distance parcourue en km jusqu'au
    point de la polyligne le plus proche de `target`, longueur totale en km)."""
    if not points or len(points) < 2:
        return None, None
    cum = 0.0
    best = None  # (distance_parcourue, distance_au_segment)
    for i in range(len(points) - 1):
        seg_len = haversine_km(*points[i], *points[i + 1])
        t, d = _project_on_segment(target, points[i], points[i + 1])
        along = cum + t * seg_len
        if best is None or d < best[1]:
            best = (along, d)
        cum += seg_len
    return best[0], cum


def _detect_departure(bus, school, now):
    """Horodatage du départ effectif du bus : premier point GPS du jour situé à
    plus de 400 m de l'école (le bus a quitté le dépôt). None si aucun point
    ne le confirme."""
    start_of_day = timezone.localtime().replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    logs = (
        bus.gps_logs.filter(timestamp__gte=start_of_day)
        .order_by("timestamp")
        .only("latitude", "longitude", "timestamp")
    )
    for log in logs:
        if (
            haversine_km(log.latitude, log.longitude, school.latitude, school.longitude)
            > _DEPARTURE_DISTANCE_KM
        ):
            return log.timestamp
    return None


def _route_progress(bus, route, now):
    """Progression du bus sur sa boucle École → arrêts → École : retourne
    (progression_attendue, progression_réelle, durée_estimée_minutes) ou None
    si les données sont insuffisantes (pas de GPS récent, départ non détecté,
    trajet sans durée ni géométrie).
    """
    if bus is None or route is None or bus.school is None:
        return None
    # Position GPS « en direct » (moins de 60 s) requise.
    if (
        bus.last_latitude is None
        or bus.last_longitude is None
        or bus.last_position_at is None
        or (now - bus.last_position_at).total_seconds() > 60
    ):
        return None
    duration_minutes = route.estimated_duration_minutes or 0.0
    if duration_minutes <= 0:
        return None
    departure = _detect_departure(bus, bus.school, now)
    if departure is None:
        return None

    elapsed_s = max(0.0, (now - departure).total_seconds())
    expected = min(1.0, elapsed_s / (duration_minutes * 60.0))

    points = route.path_geometry or []
    if not points:
        points = [(bus.school.latitude, bus.school.longitude)]
        points += [
            (rs.stop.latitude, rs.stop.longitude)
            for rs in route.stops.order_by("order")
        ]
        points.append((bus.school.latitude, bus.school.longitude))
    along, total = _distance_along_route(points, (bus.last_latitude, bus.last_longitude))
    if total is None or total <= 0:
        return None
    return expected, min(1.0, along / total), duration_minutes


def estimate_bus_delay(bus, route, now=None):
    """Estime le retard réel (minutes) d'un bus sur sa feuille de route active.

    L'horaire de référence est reconstruit à partir du départ effectif du bus
    (premier GPS du jour à plus de 400 m de l'école) : la progression attendue
    à l'instant T est (temps écoulé / durée estimée du trajet). La progression
    réelle est la fraction de la boucle École → arrêts → École déjà parcourue,
    mesurée depuis la position GPS actuelle le long de la géométrie du trajet.

    Retourne un nombre de minutes (positif = retard, négatif = avance) ou None
    si les données sont insuffisantes (bus sans GPS récent, départ non détecté,
    trajet sans durée ni géométrie).
    """
    if now is None:
        now = timezone.now()
    progress = _route_progress(bus, route, now)
    if progress is None:
        return None
    expected, actual, duration_minutes = progress
    return round((expected - actual) * duration_minutes, 1)


def estimate_remaining_minutes(bus, route, now=None):
    """Minutes restantes estimées avant le retour à l'école (ETA), d'après la
    fraction de la boucle déjà parcourue et la durée estimée du trajet.

    Retourne un nombre de minutes (≥ 0) ou None si les données sont
    insuffisantes.
    """
    if now is None:
        now = timezone.now()
    progress = _route_progress(bus, route, now)
    if progress is None:
        return None
    _, actual, duration_minutes = progress
    return round(max(0.0, 1.0 - actual) * duration_minutes, 1)


def _distribute_students(school, students, buses):
    """Réaffectation dynamique : distribue tous les élèves éligibles sur les bus
    disponibles selon leur proximité géographique ET la capacité maximale de
    chaque bus (capacity).

    Pour chaque élève (ordre stable), le bus retenu est le plus proche de son
    domicile disposant encore d'une place : la distance est mesurée aux points
    de passage habituels du bus (arrêts + géométrie de sa feuille de route),
    sinon à l'école (bus sans feuille de route — premier démarrage). Les
    égalités sont départagées par l'ordre des bus (code_bus) : le résultat est
    déterministe.

    Retourne {bus.pk: [élèves]} — les élèves restés sans bus (capacité totale
    insuffisante) n'apparaissent dans aucun groupe.
    """
    depot = (school.latitude, school.longitude)
    groups = {bus.pk: [] for bus in buses}
    for student in students:
        best_bus, best_distance = None, None
        for bus in buses:
            if len(groups[bus.pk]) >= bus.capacity:
                continue  # bus complet : capacité maximale atteinte
            route = bus.routes.order_by("-created_at").first()
            distance = _student_to_route_distance(student, route)
            if distance is None:
                # Bus sans feuille de route : référence = l'école (dépôt).
                distance = haversine_km(
                    student.latitude, student.longitude, *depot
                )
            if best_distance is None or distance < best_distance:
                best_bus, best_distance = bus, distance
        if best_bus is not None:
            groups[best_bus.pk].append(student)
    return groups


def recalculate_school_routes(school):
    """Moteur central d'affectation et d'optimisation — exécuté de façon atomique.

    Déclenché automatiquement à chaque événement structurel (position GPS du
    domicile validée ou modifiée par le parent, gel/dégel d'un élève, ajout /
    modification / suppression d'un bus, modification ou suppression d'un élève) :

    1. Filtrage : seuls les élèves actifs (non gelés) avec coordonnées GPS
       (et non absents aujourd'hui) participent à la tournée.
    2. Réaffectation dynamique : les élèves actifs sont redistribués sur les
       bus en service selon leur proximité géographique ET la capacité maximale
       (capacity) de chaque bus ; les débordements (aucune place libre) sont
       marqués en surcapacité pour l'administration.
    3. Recalcul des itinéraires : chaque bus impacté reçoit immédiatement sa
       feuille de route séquentielle optimale (TSP depuis l'école), avec
       géométrie routière réelle (OSRM, repli sur lignes droites).

    Retourne la liste des Routes créées ou mises à jour (vide si rien à faire).
    """
    if school is None:
        return []

    logger.info("Recalcul automatique des trajets de %s…", school)
    # --- Réinitialisation des alertes : l'état est recalculé à chaque événement.
    with transaction.atomic():
        Student.objects.filter(school=school).update(overcapacity_alert=False)

    students = _eligible_students(school)
    if not students:
        # Plus aucun élève éligible : on nettoie les feuilles de route.
        with transaction.atomic():
            Route.objects.filter(school=school).delete()
        return []
    buses = list(
        Bus.objects.filter(school=school, is_in_service=True).order_by("code_bus")
    )
    if not buses:
        # Aucun bus en service : les élèves concernés sont signalés à l'école.
        with transaction.atomic():
            Route.objects.filter(school=school).delete()
            Student.objects.filter(pk__in=[s.pk for s in students]).update(
                assigned_route=None, overcapacity_alert=True
            )
        return []

    groups = _distribute_students(school, students, buses)
    depot = (school.latitude, school.longitude)

    # --- Recalcul atomique des feuilles de route (TSP par bus).
    created = []
    with transaction.atomic():
        for bus in buses:
            group = groups.get(bus.pk, [])
            route = bus.routes.order_by("-created_at").first()
            if group:
                ordered = _order_students_around_depot(group, depot)
                created.append(_build_route(school, bus, ordered, route=route))
            elif route is not None:
                route.delete()  # bus sans élève : plus de feuille de route
        # Élèves restés sans bus (surcapacité) → alerte administration.
        assigned_pks = {s.pk for group in groups.values() for s in group}
        unassigned = [s for s in students if s.pk not in assigned_pks]
        if unassigned:
            Student.objects.filter(pk__in=[s.pk for s in unassigned]).update(
                assigned_route=None, overcapacity_alert=True
            )

    # Itinéraire routier réel (OSRM) — repli sur les lignes droites.
    for route in created:
        _attach_route_geometry(route)
    return created


def send_parent_sms(phone_number, message):
    """Envoie un SMS au parent via le fournisseur configuré.

    SMS_PROVIDER (variable d'environnement) :
    - 'stub' (défaut) : journalise l'envoi sans fournisseur réel (développement) ;
    - 'twilio' : API REST Twilio (SID + jeton + numéro d'expéditeur requis) ;
    - 'africastalking' : API Africa's Talking (username + clé API requis).

    Retourne True si le SMS a été envoyé (ou journalisé en mode stub), sinon False.
    """
    if not phone_number:
        logger.info("[SMS] Numéro de téléphone manquant — SMS ignoré.")
        return False

    provider = getattr(settings, "SMS_PROVIDER", "stub").lower()

    if provider == "twilio":
        sid = getattr(settings, "TWILIO_ACCOUNT_SID", "")
        token = getattr(settings, "TWILIO_AUTH_TOKEN", "")
        sender = getattr(settings, "TWILIO_FROM_NUMBER", "")
        if not (sid and token and sender):
            logger.warning("[SMS] Twilio non configuré (SID/token/numéro manquants).")
            return False
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        try:
            response = _osrm_session.post(
                url,
                data={"To": phone_number, "From": sender, "Body": message},
                auth=(sid, token),
                timeout=15,
            )
            response.raise_for_status()
            logger.info("[SMS Twilio] Envoyé à %s.", phone_number)
            return True
        except requests.RequestException as exc:
            logger.error("[SMS Twilio] Échec de l'envoi : %s", exc)
            return False

    if provider == "africastalking":
        username = getattr(settings, "AFRICAS_TALKING_USERNAME", "")
        api_key = getattr(settings, "AFRICAS_TALKING_API_KEY", "")
        sender = getattr(settings, "AFRICAS_TALKING_FROM", "")
        if not (username and api_key):
            logger.warning("[SMS] Africa's Talking non configuré (username/clé manquants).")
            return False
        url = "https://api.africastalking.com/version1/messaging"
        payload = {"username": username, "to": phone_number, "message": message}
        if sender:
            payload["from"] = sender
        try:
            response = _osrm_session.post(
                url,
                data=payload,
                headers={"apiKey": api_key, "Accept": "application/json"},
                timeout=15,
            )
            response.raise_for_status()
            logger.info("[SMS Africa's Talking] Envoyé à %s.", phone_number)
            return True
        except requests.RequestException as exc:
            logger.error("[SMS Africa's Talking] Échec de l'envoi : %s", exc)
            return False

    # Mode stub (développement) : on journalise le SMS sans fournisseur réel.
    logger.info("[SMS stub] À %s : %s", phone_number, message)
    return True


def get_road_distance_matrix(points, timeout=None):
    """Matrice des distances routières (mètres) via l'API table d'OSRM.

    points : liste ordonnée de couples (latitude, longitude), le premier
    étant le dépôt (l'école).

    Retourne (distances, durations) — matrices N×N en mètres / secondes —
    ou (None, None) si le service est injoignable (repli sur Haversine).
    """
    if not points or len(points) < 2:
        return None
    if timeout is None:
        timeout = _osrm_timeout()

    coordinates = ";".join(f"{lon},{lat}" for lat, lon in points)
    # Le service table partage le profil "driving" mais remplace "route" par "table".
    table_url = _osrm_base_url().replace("/route/v1/", "/table/v1/")
    url = f"{table_url}/{coordinates}?annotations=distance,duration"
    try:
        response = _osrm_session.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        return data["distances"], data["durations"]
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        logger.warning("Échec de l'API table OSRM (%s) — matrice Haversine.", exc)
        return None


def get_road_route_geometry(waypoints, timeout=None):
    """Interroge OSRM pour obtenir la géométrie réelle d'un itinéraire routier.

    waypoints : liste ordonnée de couples (latitude, longitude), départ et
    arrivée inclus (École → arrêts → École).

    Retourne un dict :
        {"geometry": [[lat, lon], ...], "distance_km": float, "duration_minutes": float}
    ou None si le service OSRM est injoignable, trop lent ou renvoie une
    réponse invalide (le calcul se replie alors sur la distance Haversine).
    """
    if not waypoints or len(waypoints) < 2:
        return None

    if timeout is None:
        timeout = _osrm_timeout()
    coordinates = ";".join(f"{lon},{lat}" for lat, lon in waypoints)
    url = f"{_osrm_base_url()}/{coordinates}?overview=full&geometries=geojson"
    try:
        response = _osrm_session.get(url, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        route = data["routes"][0]
        # OSRM renvoie des couples [longitude, latitude] — on convertit pour Leaflet.
        geometry = [[lat, lon] for lon, lat in route["geometry"]["coordinates"]]
        return {
            "geometry": geometry,
            "distance_km": round(route["distance"] / 1000, 2),
            "duration_minutes": round(route["duration"] / 60, 1),
        }
    except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
        logger.warning("Échec de l'interrogation OSRM (%s) — repli sur Haversine.", exc)
        return None


def _haversine_matrix(points):
    """Matrice des distances (km) entre tous les points (lat, lon)."""
    n = len(points)
    return [
        [haversine_km(*points[i], *points[j]) for j in range(n)]
        for i in range(n)
    ]


def _solve_vrp(points, capacities, matrix, time_limit_seconds=10):
    """Résout un vrai VRP avec OR-Tools.

    points : liste de (lat, lon) ; l'index 0 est le dépôt (l'école), qui sert
    à la fois de point de départ (index 0) et de retour (fin de chaque
    tournée).
    capacities : liste des capacités (nb de places) de chaque bus.
    matrix : matrice des distances en mètres (entiers), routières si OSRM
    répond, sinon Haversine.
    Retourne (manager, routing, solution) — ou (None, None, None) si échec.
    """
    n = len(points)
    num_vehicles = len(capacities)

    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_callback(from_index, to_index):
        return matrix[manager.IndexToNode(from_index)][manager.IndexToNode(to_index)]

    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Chaque élève occupe 1 place ; le dépôt (école) n'occupe rien.
    demands = [1] * n
    demands[0] = 0

    def demand_callback(from_index):
        return demands[manager.IndexToNode(from_index)]

    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  # pas de cumul initial
        capacities,  # capacité de chaque bus
        True,  # fixer le cumul à zéro au départ
        "Capacity",
    )

    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.time_limit.seconds = time_limit_seconds

    solution = routing.SolveWithParameters(search_parameters)
    return manager, routing, solution


def optimize_bus_routes(buses=None, school=None):
    """Affecte les élèves actifs non gelés et non assignés aux bus.

    Chaque trajet est une boucle fermée : départ de l'école, passage par
    les élèves (ordre optimisé), puis retour obligatoire à l'école.

    - Récupère les élèves actifs (is_active=True), non gelés
      (is_frozen=False) et non encore assignés.
    - Regroupe les élèves par bus selon sa capacité (contrainte VRP).
    - Ordonne les arrêts pour minimiser la distance totale (Haversine),
      départ et arrivée inclus à l'école.
    - Crée une instance Route par bus utilisé, avec ses RouteStop
      (boucle fermée) et initialise le suivi (élèves restants) en
      préservant la progression de ramassage (is_taken / parent_notified).

    Paramètres optionnels :
    - buses : queryset ou liste de Bus à utiliser (défaut : bus en service).
    - school : instance School à utiliser (défaut : première école de la BD).

    Retourne la liste des Routes créées.
    """
    if school is None:
        school = School.objects.filter(is_active=True).first()
    if school is None:
        raise ValueError(
            "Aucune école active définie : créez une instance School "
            "(via l'admin ou le seed) avant de lancer l'optimisation."
        )
    if not school.is_active:
        raise ValueError(
            f"L'école {school.code_ecole} est désactivée : "
            "impossible de lancer l'optimisation."
        )

    # Voisinage : on ne traite que les élèves et les bus de l'école donnée,
    # et uniquement les élèves dont le domicile a été positionné par le parent
    # et qui ne sont pas déclarés absents aujourd'hui.
    students = [
        s
        for s in Student.objects.filter(
            school=school,
            is_active=True,
            is_frozen=False,
            assigned_route__isnull=True,
            latitude__isnull=False,
            longitude__isnull=False,
        ).order_by("nom", "prenom")
        if s.id not in absent_student_ids()
    ]
    if not students:
        return []

    if buses is None:
        buses = Bus.objects.filter(school=school, is_in_service=True).order_by("code_bus")
    buses = list(buses)
    if not buses:
        return []

    total_capacity = sum(bus.capacity for bus in buses)
    if total_capacity < len(students):
        raise ValueError(
            f"Capacité totale insuffisante : {len(students)} élèves "
            f"pour {total_capacity} places de bus."
        )

    # Point 0 = l'école (dépôt = départ ET retour de chaque tournée),
    # puis un point par élève.
    depot = (school.latitude, school.longitude)
    points = [depot] + [(s.latitude, s.longitude) for s in students]
    capacities = [bus.capacity for bus in buses]

    # Matrice de distances réelles par la route (OSRM) si possible ;
    # sinon repli sur la matrice Haversine (lignes droites).
    road = get_road_distance_matrix(points)
    if road is not None:
        distances_m, _ = road
        matrix = [
            [
                int(round(d))
                if d is not None
                else int(round(haversine_km(*points[i], *points[j]) * 1000))
                for j, d in enumerate(row)
            ]
            for i, row in enumerate(distances_m)
        ]
    else:
        matrix = [
            [int(round(d * 1000)) for d in row]
            for row in _haversine_matrix(points)
        ]

    manager, routing, solution = _solve_vrp(points, capacities, matrix)
    if solution is None:
        raise RuntimeError("OR-Tools n'a pas trouvé de solution.")

    created_routes = []
    with transaction.atomic():
        for vehicle_id, bus in enumerate(buses):
            # Élèves assignés à ce véhicule, dans l'ordre de visite.
            assigned = []
            index = routing.Start(vehicle_id)
            while not routing.IsEnd(index):
                node = manager.IndexToNode(index)
                if node != 0:  # 0 = dépôt (école)
                    assigned.append(students[node - 1])
                index = solution.Value(routing.NextVar(index))

            if not assigned:
                continue

            created_routes.append(_build_route(school, bus, assigned))

    # Amélioration hors transaction (appel réseau) : itinéraire routier réel.
    for route in created_routes:
        _attach_route_geometry(route)

    return created_routes


# ---------------------------------------------------------------- Réoptimisation dynamique (onboarding)


def _order_students_around_depot(students, depot):
    """Ordre optimal de passage (TSP mono-véhicule résolu par OR-Tools, matrice
    Haversine — aucun appel réseau) depuis le dépôt (l'école).

    Repli : ordre d'origine si OR-Tools échoue (ne bloque jamais l'onboarding).
    """
    if len(students) <= 2:
        return list(students)
    points = [depot] + [(s.latitude, s.longitude) for s in students]
    matrix = _haversine_matrix(points)
    matrix_int = [[int(round(d * 1000)) for d in row] for row in matrix]
    manager, routing, solution = _solve_vrp(points, [len(students)], matrix_int)
    if solution is None:
        return list(students)
    ordered = []
    index = routing.Start(0)
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != 0:  # 0 = dépôt (école)
            ordered.append(students[node - 1])
        index = solution.Value(routing.NextVar(index))
    return ordered


def reoptimize_bus_route(bus, timeout=12):
    """Recalcule la feuille de route d'un bus après l'affectation d'un nouvel élève :

    1. ordre optimal de passage (TSP OR-Tools, Haversine) depuis l'école ;
    2. géométrie routière réelle via OSRM (repli : lignes droites) pour que la
       carte du chauffeur reste lisible immédiatement.

    Ne perturbe ni les autres bus ni les compteurs de ramassage en cours.
    Retourne la Route recalculée, ou None si le bus n'a aucun élève à charger.
    """
    school = bus.school
    if school is None:
        return None

    students = list(
        Student.objects.filter(
            assigned_route__bus=bus,
            is_active=True,
            is_frozen=False,
            latitude__isnull=False,
            longitude__isnull=False,
        ).order_by("nom", "prenom")
    )
    absent = absent_student_ids()
    active = [s for s in students if s.id not in absent]
    if not active:
        return None

    depot = (school.latitude, school.longitude)
    ordered = _order_students_around_depot(active, depot)

    route = bus.routes.order_by("-created_at").first()
    if route is None:
        route = Route.objects.create(
            name=f"Route {bus.code_bus} — {timezone.localdate().isoformat()}",
            bus=bus,
            school=school,
            students_taken=sum(1 for s in ordered if s.is_taken),
            students_remaining=sum(1 for s in ordered if not s.is_taken),
        )

    # --- Regroupement spatial ---
    clusters = cluster_students_by_proximity(ordered)
    clusters.sort(key=lambda c: haversine_km(depot[0], depot[1], c['centroid'][0], c['centroid'][1]))

    with transaction.atomic():
        # Réordonne les arrêts du trajet existant (ou en crée).
        route.stops.all().delete()
        all_students_update = []
        for order, cluster in enumerate(clusters, start=1):
            centroid_lat, centroid_lon = cluster['centroid']
            n_students = len(cluster['students'])
            if n_students > 1:
                stop_name = f"Arrêt Zone {order} ({n_students} élèves)"
            else:
                s = cluster['students'][0]
                stop_name = f"Arrêt {s.prenom} {s.nom}"
            stop, _ = BusStop.objects.get_or_create(
                name=stop_name,
                defaults={
                    "latitude": centroid_lat,
                    "longitude": centroid_lon,
                },
            )
            if stop.latitude != centroid_lat or stop.longitude != centroid_lon:
                stop.latitude = centroid_lat
                stop.longitude = centroid_lon
                stop.save(update_fields=["latitude", "longitude"])
            RouteStop.objects.create(route=route, stop=stop, order=order)
            for student in cluster['students']:
                student.assigned_route = route
                all_students_update.append(student)
        Student.objects.bulk_update(all_students_update, ["assigned_route"])

        # Boucle fermée École → arrêts → École + compteurs de ramassage.
        ordered_points = [depot] + [c['centroid'] for c in clusters] + [depot]
        total_km = sum(
            haversine_km(*ordered_points[i], *ordered_points[i + 1])
            for i in range(len(ordered_points) - 1)
        )
        route.total_distance_km = round(total_km, 2)
        route.estimated_duration_minutes = round(
            total_km / AVERAGE_SPEED_KMH * 60, 1
        )
        route.students_remaining = sum(1 for s in ordered if not s.is_taken)
        route.save(
            update_fields=[
                "total_distance_km",
                "estimated_duration_minutes",
                "students_remaining",
            ]
        )

    # Itinéraire routier réel (OSRM, délai court) — repli sur les lignes droites.
    waypoints = [depot] + [c['centroid'] for c in clusters] + [depot]
    road = get_road_route_geometry(waypoints, timeout=timeout)
    if road:
        route.path_geometry = road["geometry"]
        route.total_distance_km = road["distance_km"]
        route.estimated_duration_minutes = road["duration_minutes"]
        route.save(
            update_fields=[
                "path_geometry",
                "total_distance_km",
                "estimated_duration_minutes",
            ]
        )
    else:
        route.path_geometry = [[lat, lon] for lat, lon in ordered_points]
        route.save(update_fields=["path_geometry"])

    return route
