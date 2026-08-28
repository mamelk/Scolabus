/* ============================================================================
 * Scolaloop — Service Worker (static/js/sw.js)
 * ----------------------------------------------------------------------------
 * Rend l'application utilisable en mode hors-ligne et à très bas débit :
 *  - précache des assets statiques (logo, manifest, JS) et des bibliothèques
 *    CDN (Leaflet, Font Awesome) ;
 *  - cache des pages d'accueil de chaque rôle (Chauffeur, Parent, École) dès
 *    leur première visite (réseau d'abord, repli sur le cache) ;
 *  - cache local des tuiles OpenStreetMap de la ville (cache-first, taille
 *    plafonnée) : les tuiles déjà vues sont servies instantanément ;
 *  - les requêtes API (position GPS, live parent, synchronisation) ne sont
 *    JAMAIS mises en cache.
 *
 * Servi à la racine (/sw.js) par la vue Django `pwa_service_worker` avec
 * l'en-tête `Service-Worker-Allowed: /` : sa portée couvre tout le site.
 *
 * Pour publier une nouvelle version des statiques, incrémenter VERSION.
 * ========================================================================== */

'use strict';

var VERSION = 'scolaloop-v1.1.0';
var STATIC_CACHE = VERSION + '-static';
var PAGE_CACHE = VERSION + '-pages';
var TILE_CACHE = VERSION + '-tiles';

// Nombre maximal de tuiles conservées localement (evite de saturer le stockage).
var TILE_MAX_ENTRIES = 3000;

/* Assets statiques du site (même origine). */
var STATIC_ASSETS = [
    '/static/manifest.json',
    '/static/images/logo.svg',
    '/static/offline.html',
    '/static/js/offline_sync.js',
    '/static/js/driver_tracking.js',
];

/* Bibliothèques CDN nécessaires au rendu des cartes hors-ligne. */
var CDN_ASSETS = [
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css',
    'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
    'https://cdnjs.cloudflare.com/ajax/libs/leaflet-polylinedecorator/1.6.0/leaflet.polylineDecorator.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.2/css/all.min.css',
    'https://cdn.tailwindcss.com',
];

/* Tuile transparente 1x1 : repli lorsque la tuile demandée n'est pas en cache
 * et que le réseau est indisponible (aucune image cassée sur la carte). */
var BLANK_TILE =
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==';

/* Pages publiques mises en cache à l'installation (aucune authentification). */
var PUBLIC_PAGES = ['/', '/login/', '/apropos/'];

var PRECACHE_URLS = STATIC_ASSETS.concat(CDN_ASSETS, PUBLIC_PAGES);

/* ---------------------------------------------------------------------------
 * Installation : préchargement des assets.
 * ------------------------------------------------------------------------- */
self.addEventListener('install', function (event) {
    event.waitUntil(
        caches.open(STATIC_CACHE)
            .then(function (cache) {
                // allSettled : une ressource indisponible ne bloque pas le reste.
                return Promise.allSettled(
                    PRECACHE_URLS.map(function (url) { return cache.add(url); })
                );
            })
            .then(function () { return self.skipWaiting(); })
    );
});

/* ---------------------------------------------------------------------------
 * Activation : suppression des anciennes versions de caches.
 * ------------------------------------------------------------------------- */
self.addEventListener('activate', function (event) {
    event.waitUntil(
        caches.keys()
            .then(function (keys) {
                return Promise.all(
                    keys
                        .filter(function (key) { return key.indexOf(VERSION) !== 0; })
                        .map(function (key) { return caches.delete(key); })
                );
            })
            .then(function () { return self.clients.claim(); })
    );
});

/* ---------------------------------------------------------------------------
 * Utilitaires.
 * ------------------------------------------------------------------------- */
function isApiRequest(url) {
    // Les endpoints JSON ne doivent jamais être servis depuis le cache.
    if (url.origin !== self.location.origin) return false;
    return url.pathname.indexOf('/api/') === 0;
}

function isStaticAsset(url) {
    if (url.origin === self.location.origin && url.pathname.indexOf('/static/') === 0) {
        return true;
    }
    // Bibliothèques CDN (feuilles de style, scripts, polices, icônes).
    var host = url.hostname;
    return host === 'unpkg.com'
        || host === 'cdnjs.cloudflare.com'
        || host === 'fonts.googleapis.com'
        || host === 'fonts.gstatic.com'
        || host === 'cdn.tailwindcss.com';
}

/* Réseau d'abord, cache en secours — pour les pages HTML (navigations). */
function navigationStrategy(request) {
    var requestUrl = request.url;
    return caches.open(PAGE_CACHE).then(function (cache) {
        return fetch(request)
            .then(function (response) {
                // Seules les pages HTTP 200 sont mises en cache (pas les
                // redirections vers /login/ par exemple).
                if (response && response.ok) {
                    cache.put(request, response.clone());
                }
                return response;
            })
            .catch(function () {
                // Hors-ligne : version en cache, puis page d'accueil, puis page de repli.
                return cache.match(requestUrl)
                    .then(function (cached) { return cached || cache.match('/'); })
                    .then(function (cached) {
                        return cached || caches.match('/static/offline.html');
                    });
            });
    });
}

/* Cache d'abord, réseau en secours — pour les assets statiques. */
function staleWhileRevalidate(request) {
    return caches.open(STATIC_CACHE).then(function (cache) {
        return cache.match(request).then(function (cached) {
            var network = fetch(request)
                .then(function (response) {
                    if (response && response.ok) {
                        cache.put(request, response.clone());
                    }
                    return response;
                })
                .catch(function () { return cached; });
            return cached || network;
        });
    });
}

/* Cache d'abord pour les tuiles OpenStreetMap (stockage local de la ville). */
function tileStrategy(request) {
    return caches.open(TILE_CACHE).then(function (cache) {
        return cache.match(request).then(function (cached) {
            if (cached) return cached;
            return fetch(request)
                .then(function (response) {
                    if (response && response.ok) {
                        cache.put(request, response.clone());
                        trimTileCache(cache);
                    }
                    return response;
                })
                .catch(function () {
                    // Hors-ligne : tuile transparente plutôt qu'une image cassée.
                    return new Response(BLANK_TILE, {
                        headers: { 'Content-Type': 'image/png' }
                    });
                });
        });
    });
}

/* Supprime les tuiles les plus anciennes au-delà du plafond. */
function trimTileCache(cache) {
    cache.keys().then(function (keys) {
        if (keys.length <= TILE_MAX_ENTRIES) return;
        var toDelete = keys.slice(0, keys.length - TILE_MAX_ENTRIES);
        return Promise.all(toDelete.map(function (key) { return cache.delete(key); }));
    }).catch(function () { /* stockage plein : on ignore */ });
}

/* ---------------------------------------------------------------------------
 * Interception des requêtes.
 * ------------------------------------------------------------------------- */
self.addEventListener('fetch', function (event) {
    var request = event.request;
    var url = new URL(request.url);

    // Les écritures (POST…) et les endpoints API ne sont jamais mis en cache.
    if (request.method !== 'GET' || isApiRequest(url)) {
        return; // comportement réseau par défaut
    }

    // Tuiles OpenStreetMap : cache local prioritaire.
    if (url.hostname.indexOf('tile.openstreetmap.org') !== -1) {
        event.respondWith(tileStrategy(request));
        return;
    }

    // Navigations (pages HTML) : réseau d'abord, cache en secours.
    if (request.mode === 'navigate') {
        event.respondWith(navigationStrategy(request));
        return;
    }

    // Assets statiques (même origine ou CDN) : cache d'abord.
    if (isStaticAsset(url)) {
        event.respondWith(staleWhileRevalidate(request));
    }
    // Toute autre requête : comportement réseau par défaut.
});
