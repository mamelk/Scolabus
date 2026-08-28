/* ============================================================================
 * Scolaloop — Synchronisation hors-ligne (static/js/offline_sync.js)
 * ----------------------------------------------------------------------------
 * File d'attente locale (localStorage) des données accumulées pendant une
 * coupure réseau : points GPS {latitude, longitude, speed, timestamp} et
 * incidents. À chaque retour du réseau, toutes les données en attente sont
 * envoyées en un seul appel groupé à l'endpoint Django /api/sync-offline-data/.
 *
 * API exposée sur window.ScolaloopOffline :
 *   - enqueuePoint(latitude, longitude, speed, timestamp)
 *   - enqueueIncident(typeIncident, description)
 *   - pendingCount()        → nombre d'éléments en attente
 *   - isOnline()
 *   - syncData()            → tentative de resynchronisation immédiate
 *
 * Événements émis (écoutables pour rafraîchir l'interface) :
 *   - 'scolaloop:connectivity'  { online, pending }
 *   - 'scolaloop:queuechange'   { pending }
 *   - 'scolaloop:sync'          { synced_points, synced_incidents }
 * ========================================================================== */

(function () {
    'use strict';

    var QUEUE_KEY = 'scolaloop_offline_queue_v1';
    var SYNC_URL = '/api/sync-offline-data/';
    var MAX_QUEUE_ITEMS = 5000; // garde-fou : au-delà, les plus anciens sont écartés

    function readQueue() {
        try {
            var raw = localStorage.getItem(QUEUE_KEY);
            var queue = raw ? JSON.parse(raw) : [];
            return Array.isArray(queue) ? queue : [];
        } catch (err) {
            return [];
        }
    }

    function writeQueue(queue) {
        try {
            // On conserve les éléments les plus récents si la file dépasse la limite.
            localStorage.setItem(QUEUE_KEY, JSON.stringify(queue.slice(-MAX_QUEUE_ITEMS)));
        } catch (err) {
            // Stockage indisponible (mode privé, quota) : on continue sans file locale.
        }
    }

    function csrfToken() {
        var el = document.querySelector('[name=csrfmiddlewaretoken]');
        if (el) return el.value;
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function emit(name, detail) {
        window.dispatchEvent(new CustomEvent(name, { detail: detail || {} }));
    }

    function enqueue(record) {
        var queue = readQueue();
        queue.push(record);
        writeQueue(queue);
        emit('scolaloop:queuechange', { pending: queue.length });
        return queue.length;
    }

    /* Envoie toutes les données en attente au serveur en un seul appel groupé.
     * Ne fait rien si le navigateur est hors-ligne ou si la file est vide. */
    function syncData() {
        if (!navigator.onLine) {
            return Promise.resolve({ skipped: true, synced_points: 0, synced_incidents: 0 });
        }
        var queue = readQueue();
        if (queue.length === 0) {
            return Promise.resolve({ skipped: true, synced_points: 0, synced_incidents: 0 });
        }

        var points = queue
            .filter(function (r) { return r.kind === 'point'; })
            .map(function (r) {
                return {
                    latitude: r.latitude,
                    longitude: r.longitude,
                    speed: r.speed || 0,
                    timestamp: r.timestamp || new Date().toISOString(),
                };
            });
        var incidents = queue
            .filter(function (r) { return r.kind === 'incident'; })
            .map(function (r) {
                return {
                    type_incident: r.type_incident,
                    description: r.description || '',
                    timestamp: r.timestamp || new Date().toISOString(),
                };
            });

        return fetch(SYNC_URL, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken(),
            },
            body: JSON.stringify({ points: points, incidents: incidents }),
        })
            .then(function (resp) {
                if (!resp.ok) return { skipped: false, synced_points: 0, synced_incidents: 0 };
                return resp.json();
            })
            .then(function (result) {
                if (result && result.ok) {
                    // Le serveur a tout reçu : on vide la file d'attente locale.
                    writeQueue([]);
                    emit('scolaloop:queuechange', { pending: 0 });
                    emit('scolaloop:sync', result);
                }
                return result || { skipped: false, synced_points: 0, synced_incidents: 0 };
            })
            .catch(function () {
                // Réseau instable : les données restent en file pour une prochaine tentative.
                return { skipped: false, synced_points: 0, synced_incidents: 0 };
            });
    }

    function notifyConnectivity() {
        emit('scolaloop:connectivity', {
            online: navigator.onLine,
            pending: readQueue().length,
        });
    }

    var api = {
        enqueuePoint: function (latitude, longitude, speed, timestamp) {
            return enqueue({
                kind: 'point',
                latitude: latitude,
                longitude: longitude,
                speed: speed || 0,
                timestamp: timestamp || new Date().toISOString(),
            });
        },
        enqueueIncident: function (typeIncident, description) {
            return enqueue({
                kind: 'incident',
                type_incident: typeIncident,
                description: description || '',
                timestamp: new Date().toISOString(),
            });
        },
        pendingCount: function () {
            return readQueue().length;
        },
        isOnline: function () {
            return navigator.onLine;
        },
        syncData: syncData,
    };

    window.ScolaloopOffline = api;

    // Resynchronisation automatique au retour de la connexion.
    window.addEventListener('online', function () {
        notifyConnectivity();
        syncData();
    });
    window.addEventListener('offline', notifyConnectivity);

    // À l'ouverture de la page : état initial + resynchronisation si nécessaire.
    document.addEventListener('DOMContentLoaded', function () {
        notifyConnectivity();
        if (navigator.onLine) syncData();
    });
})();
