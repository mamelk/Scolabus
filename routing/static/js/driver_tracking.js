/* ============================================================================
 * Scolaloop — Suivi GPS hors-ligne du chauffeur (static/js/driver_tracking.js)
 * ----------------------------------------------------------------------------
 * Envoie la position GPS du bus au serveur ; si l'appel échoue (absence de
 * réseau ou délai dépassé), la coordonnée {latitude, longitude, speed,
 * timestamp} est enregistrée dans la file d'attente locale (localStorage,
 * gérée par offline_sync.js) en vue d'une resynchronisation automatique.
 *
 * Affiche aussi l'indicateur de connectivité sur l'écran chauffeur :
 *   🟢 En ligne                 (vert)
 *   🟠 Hors-ligne (X en attente) (orange)
 *   🟡 Synchronisation… (X)      (ambre, en ligne avec données en attente)
 *
 * Dépend de window.ScolaloopOffline (offline_sync.js) — à charger AVANT.
 * API exposée sur window.ScolaloopTracking :
 *   - sendPosition(lat, lng, speedKmh) → {ok, queued, pending}
 *   - reportIncident(type, description) → {ok, queued, pending}
 * ========================================================================== */

(function () {
    'use strict';

    var POSITION_URL = '/driver/position/';
    var INCIDENT_URL = '/driver/incident/';
    var SEND_TIMEOUT_MS = 8000; // très bas débit : on n'attend jamais indéfiniment

    function csrfToken() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el) return el.value;
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function pendingCount() {
        return window.ScolaloopOffline ? window.ScolaloopOffline.pendingCount() : 0;
    }

    function updateIndicator() {
        var badge = document.getElementById('conn-badge');
        if (!badge) return;
        var text = document.getElementById('conn-text');
        var online = navigator.onLine;
        var pending = pendingCount();

        if (online && pending === 0) {
            badge.className = 'conn-badge online';
            text.textContent = '🟢 En ligne';
        } else if (online) {
            badge.className = 'conn-badge syncing';
            text.textContent = '🟡 Synchronisation… (' + pending + ' en attente)';
        } else {
            badge.className = 'conn-badge offline';
            text.textContent = '🟠 Hors-ligne (' + pending + ' point' + (pending > 1 ? 's' : '') + ' en attente)';
        }
    }

    /* POST avec délai maximal : au-delà, on considère l'envoi comme échoué. */
    function postWithTimeout(url, body) {
        var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
        var timer = controller ? setTimeout(function () { controller.abort(); }, SEND_TIMEOUT_MS) : null;
        var options = {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': csrfToken(),
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            body: new URLSearchParams(body),
        };
        if (controller) options.signal = controller.signal;
        return fetch(url, options).then(function (resp) {
            if (timer) clearTimeout(timer);
            return resp;
        }, function (err) {
            if (timer) clearTimeout(timer);
            throw err;
        });
    }

    /* Envoie la position au serveur ; en cas d'échec réseau ou de délai
     * dépassé, la coordonnée est mise en file d'attente locale. */
    function sendPosition(latitude, longitude, speedKmh) {
        var body = { latitude: latitude, longitude: longitude };
        if (speedKmh != null && speedKmh > 0) body.speed = speedKmh;

        return postWithTimeout(POSITION_URL, body)
            .then(function (resp) {
                updateIndicator();
                return { ok: resp.ok, queued: false, pending: pendingCount() };
            })
            .catch(function () {
                // Hors-ligne ou timeout : on enregistre la coordonnée localement.
                var pending = pendingCount();
                if (window.ScolaloopOffline) {
                    pending = window.ScolaloopOffline.enqueuePoint(
                        body.latitude, body.longitude, body.speed || 0, new Date().toISOString()
                    );
                    // Tentative immédiate de resynchronisation (sans frais si hors-ligne).
                    window.ScolaloopOffline.syncData();
                }
                updateIndicator();
                return { ok: false, queued: true, pending: pending };
            });
    }

    /* Signale un incident ; hors-ligne, il est mis en file pour resynchronisation. */
    function reportIncident(typeIncident, description) {
        return postWithTimeout(INCIDENT_URL, {
            type_incident: typeIncident,
            description: description || '',
        })
            .then(function (resp) {
                updateIndicator();
                return { ok: resp.ok, queued: false, pending: pendingCount() };
            })
            .catch(function () {
                var pending = pendingCount();
                if (window.ScolaloopOffline) {
                    pending = window.ScolaloopOffline.enqueueIncident(typeIncident, description);
                    window.ScolaloopOffline.syncData();
                }
                updateIndicator();
                return { ok: false, queued: true, pending: pending };
            });
    }

    // L'indicateur suit l'état de la connexion et de la file d'attente.
    window.addEventListener('scolaloop:connectivity', updateIndicator);
    window.addEventListener('scolaloop:queuechange', updateIndicator);
    window.addEventListener('scolaloop:sync', updateIndicator);
    document.addEventListener('DOMContentLoaded', updateIndicator);

    window.ScolaloopTracking = {
        sendPosition: sendPosition,
        reportIncident: reportIncident,
        updateIndicator: updateIndicator,
        pendingCount: pendingCount,
    };
})();
