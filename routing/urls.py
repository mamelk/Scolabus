from django.urls import path

from . import views

app_name = "routing"

urlpatterns = [
    path("", views.home_view, name="home"),
    # SEO : robots.txt et sitemap.xml
    path("robots.txt", views.robots_txt_view, name="robots_txt"),
    path("sitemap.xml", views.sitemap_xml_view, name="sitemap_xml"),
    # PWA : Service Worker servi à la racine (portée "/")
    path("sw.js", views.pwa_service_worker, name="service_worker"),
    path("apropos/", views.about_view, name="about"),
    path("login/", views.login_view, name="login"),
    path("logout/", views.logout_view, name="logout"),
    path("register-school/", views.register_school_view, name="register_school"),
    path("change-password/", views.change_password_view, name="change_password"),
    # Administration générale (écoles) — réservée aux superutilisateurs
    path("ecoles/", views.schools_admin_view, name="schools_admin"),
    path("ecoles/<int:pk>/toggle/", views.toggle_school_active, name="toggle_school_active"),
    path("ecoles/<int:pk>/supprimer/", views.school_delete, name="school_delete"),
    path("ecoles/<int:pk>/reset-password/", views.school_reset_password, name="school_reset_password"),
    path("dashboard/", views.dashboard_view, name="dashboard"),
    # Chauffeur
    path("driver/", views.driver_view, name="driver"),
    path("driver/pickup/<int:pk>/", views.driver_pickup_student, name="driver_pickup"),
    path("driver/position/", views.driver_update_position, name="driver_position"),
    path("driver/incident/", views.driver_report_incident, name="driver_incident"),
    path("api/driver/route/", views.driver_route_api, name="driver_route_api"),
    # Synchronisation groupée des données accumulées hors-ligne
    path("api/sync-offline-data/", views.sync_offline_data, name="sync_offline_data"),
    # Parent / Élève
    path("parent/", views.parent_dashboard_view, name="parent"),
    path("parent/setup-home/", views.parent_setup_home_view, name="parent_setup_home"),
    path("parent/absence/", views.parent_toggle_absence, name="parent_absence"),
    path("api/parent/live/", views.parent_live, name="parent_live"),
    # Localisation temps réel : un bus précis ou la flotte entière de l'école
    path("api/bus/<int:bus_id>/location/", views.bus_location_api, name="bus_location_api"),
    path("api/school/fleet/", views.school_fleet_api, name="school_fleet_api"),
    # SMS d'urgence (école)
    path("dashboard/broadcast/", views.broadcast_sms_view, name="broadcast_sms"),
    # Maintenance flotte (école)
    path("dashboard/maintenance/ajouter/", views.maintenance_add, name="maintenance_add"),
    path("dashboard/maintenance/<int:pk>/supprimer/", views.maintenance_delete, name="maintenance_delete"),
    # Replay GPS (école)
    path("dashboard/replay/<int:bus_id>/", views.replay_view, name="replay"),
    path("statistiques/", views.statistics_view, name="statistics"),
    # Élèves
    path("eleves/ajouter/", views.student_add, name="student_add"),
    path("eleves/<int:pk>/modifier/", views.student_edit, name="student_edit"),
    path("eleves/<int:pk>/supprimer/", views.student_delete, name="student_delete"),
    path("eleves/<int:pk>/geler/", views.toggle_freeze_student, name="toggle_freeze_student"),
    path("eleves/<int:pk>/reset-password/", views.student_reset_password, name="student_reset_password"),
    # Bus
    path("bus/ajouter/", views.bus_add, name="bus_add"),
    path("bus/<int:pk>/modifier/", views.bus_edit, name="bus_edit"),
    path("bus/<int:pk>/supprimer/", views.bus_delete, name="bus_delete"),
    path("bus/<int:pk>/reset-password/", views.bus_reset_password, name="bus_reset_password"),
    # Imports Excel
    path("import/eleves/", views.upload_students, name="upload_students"),
    path("import/eleves/geler/", views.upload_students_freeze, name="upload_students_freeze"),
    path("import/bus/", views.upload_buses, name="upload_buses"),
    # Export Excel
    path("export/eleves-par-bus/", views.export_students_by_bus, name="export_students_by_bus"),
    path("export/eleves-bus/<int:pk>/", views.export_bus_students, name="export_bus_students"),
    path("export/feuille-route/<int:pk>/", views.route_sheet_pdf, name="route_sheet_pdf"),
]
