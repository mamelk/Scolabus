/* ============================================================================
 * Scolaloop — Carte du chauffeur (static/js/driver_map.js)
 * ----------------------------------------------------------------------------
 * Carte Leaflet de l'écran chauffeur, optimisée pour la lecture rapide de loin :
 *
 *  Marqueurs élèves (3 états distincts) :
 *    🟢 Vert  #22c55e  — élève en attente de ramassage
 *    ⚪ Gris  #94a3b8  — élève déjà ramassé/déposé (coche)
 *    🔴 Rouge #ef4444  — élève signalé absent aujourd'hui
 *
 *  Tracé du parcours (haute lisibilité) :
 *    ligne néon jaune fluo (#FFE600), épaisseur 8, opacité 0,95,
 *    avec bordure de contraste sombre en dessous + flèches de direction.
 *
 * API exposée sur window.ScolaloopDriverMap :
 *   - init(mapData)  → { map, busMarker, markers, updateRoute, setStudentState }
 *   - updateRoute(routeData)  → redessine tracé + marqueurs (rafraîchissement auto)
 *   - setStudentState(marker, state)  → 'waiting' | 'taken' | 'absent'
 * ========================================================================== */

(function () {
    'use strict';

    var NEON = '#FFE600';            // jaune fluo
    var NEON_BORDER = '#0f172a';     // bordure sombre de contraste
    var COLOR_WAITING = '#22c55e';   // 🟢 en attente
    var COLOR_TAKEN = '#94a3b8';     // ⚪ ramassé/déposé
    var COLOR_ABSENT = '#ef4444';    // 🔴 absent

    var map = null;
    var busMarker = null;
    var markers = {};                // id élève → marqueur Leaflet
    var routeLayers = null;          // { border, neon, arrows }
    var DEFAULT_ZOOM = 17;           // rues/avenues lisibles sans zoom manuel

    function studentState(s) {
        if (s.absent) return 'absent';
        if (s.taken) return 'taken';
        return 'waiting';
    }

    function studentIcon(state) {
        var color, html;
        if (state === 'absent') {
            color = COLOR_ABSENT;
            html = '<i class="fa-solid fa-user-slash"></i>';
        } else if (state === 'taken') {
            color = COLOR_TAKEN;
            html = '<i class="fa-solid fa-user-check"></i>';
        } else {
            color = COLOR_WAITING;
            html = '<i class="fa-solid fa-user-graduate"></i>';
        }
        return L.divIcon({
            className: '',
            html: '<div class="map-icon student" style="background:' + color + ';">' + html + '</div>',
            iconSize: [38, 38],
            iconAnchor: [19, 19]
        });
    }

    function studentPopup(s) {
        var stateLabel = s.absent ? '🚫 Absent aujourd\'hui'
            : (s.taken ? '✅ Ramassé' : '⏳ En attente');
        return '<strong>' + s.name + '</strong><br>' + s.matricule + '<br>' + stateLabel;
    }

    /* Tracé néon haute visibilité : bordure sombre épaisse sous la ligne fluo. */
    function drawRoute(stops) {
        if (routeLayers) {
            if (routeLayers.border) map.removeLayer(routeLayers.border);
            if (routeLayers.neon) map.removeLayer(routeLayers.neon);
            if (routeLayers.arrows) map.removeLayer(routeLayers.arrows);
            routeLayers = null;
        }
        if (!stops || stops.length < 2) return;

        var border = L.polyline(stops, {
            color: NEON_BORDER, weight: 14, opacity: 0.55, lineJoin: 'round'
        }).addTo(map);
        var neon = L.polyline(stops, {
            color: NEON, weight: 8, opacity: 0.95, lineJoin: 'round'
        }).addTo(map);
        var arrows = L.polylineDecorator(neon, {
            patterns: [{
                offset: 22, repeat: 120,
                symbol: L.Symbol.arrowHead({
                    pixelSize: 14, polygon: false,
                    pathOptions: { stroke: true, color: NEON_BORDER, weight: 3 }
                })
            }]
        }).addTo(map);
        routeLayers = { border: border, neon: neon, arrows: arrows };
        // Pas de fitBounds : la carte reste zoomée sur le bus (auto-centrage du
        // suivi GPS) au lieu de se dézoomer sur toute la tournée.
    }

    /* Redessine la carte à partir des données de la feuille de route. */
    function updateRoute(routeData) {
        if (!map) return;

        var students = routeData.students || [];
        var seen = {};

        // Marqueurs élèves : création, mise à jour d'état, retrait.
        students.forEach(function (s) {
            seen[s.id] = true;
            var state = studentState(s);
            if (markers[s.id]) {
                var m = markers[s.id];
                m.setLatLng([s.latitude, s.longitude]);
                m.setIcon(studentIcon(state));
                m.bindPopup(studentPopup(s));
            } else {
                var marker = L.marker([s.latitude, s.longitude], { icon: studentIcon(state) })
                    .addTo(map)
                    .bindPopup(studentPopup(s));
                markers[s.id] = marker;
            }
        });
        Object.keys(markers).forEach(function (id) {
            if (!seen[id]) {
                map.removeLayer(markers[id]);
                delete markers[id];
            }
        });

        // Tracé : géométrie routière réelle, sinon lignes droites élèves restants.
        var path = routeData.route && routeData.route.path_geometry;
        var stops = path && path.length
            ? path
            : students
                .filter(function (s) { return !s.taken && !s.absent; })
                .map(function (s) { return [s.latitude, s.longitude]; });
        drawRoute(stops);
    }

    function init(mapData) {
        var start = [mapData.bus.latitude || 0, mapData.bus.longitude || 0];
        // Zoom par défaut élevé : les noms de rues/avenues sont immédiatement
        // lisibles ; l'auto-centrage du suivi GPS conserve ce niveau de zoom.
        map = L.map('map').setView(start, DEFAULT_ZOOM);
        L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 19,
            attribution: '&copy; OpenStreetMap'
        }).addTo(map);

        // Icône de bus surdimensionnée (64 px) : très visible au centre de
        // l'écran, même sur des avenues étroites.
        var busIcon = L.divIcon({
            className: '',
            html: '<div class="map-icon bus" style="background:#ffffff;"><img src="/static/images/bus_icon.svg" alt="Bus" style="width:100%;height:100%;border-radius:50%;"></div>',
            iconSize: [64, 64],
            iconAnchor: [32, 32]
        });
        busMarker = L.marker(start, { icon: busIcon, zIndexOffset: 1000 }).addTo(map)
            .bindPopup('<strong>🚌 ' + mapData.bus.code_bus + '</strong><br>Position GPS en direct');

        updateRoute(mapData);
        return {
            map: map,
            busMarker: busMarker,
            markers: markers,
            updateRoute: updateRoute,
            setStudentState: function (marker, state) {
                if (marker) marker.setIcon(studentIcon(state));
            },
        };
    }

    window.ScolaloopDriverMap = {
        init: init,
        // updateRoute partage les fermetures du module : à utiliser après init().
        updateRoute: updateRoute,
        setStudentState: function (marker, state) {
            if (marker) marker.setIcon(studentIcon(state));
        },
    };
})();
