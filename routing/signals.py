"""Signaux Django de l'application routing — système 100 % événementiel.

Toute modification structurelle relance automatiquement le moteur central
``recalculate_school_routes(school)``, qui redistribue les élèves actifs
(non gelés, avec GPS) sur les bus disponibles (capacité + proximité) et
reconstruit la feuille de route optimale (TSP/VRP) de chaque bus impacté :

- 📍 Localisation parent : le parent enregistre ou modifie le domicile GPS
  de l'élève (latitude / longitude) ;
- ❄️ Gel / suspension : un élève est gelé, dégelé, désactivé ou réactivé ;
- 🚍 Gestion de la flotte : ajout, modification (ex. capacité) ou
  suppression d'un bus ;
- 🗑️ Élève : création, modification du profil/adresse ou suppression.

Aucune action manuelle n'est requise : les boutons du tableau de bord ont
disparu. Les recalculs sont suspendus pendant les imports en masse
(contextmanager ``recalc_hold``) puis déclenchés une seule fois à la fin.
"""

import logging
import threading
from contextlib import contextmanager

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Bus, Student
from .services import recalculate_school_routes

logger = logging.getLogger(__name__)

# Champs dont la modification doit relancer le moteur central.
_STUDENT_RECALC_FIELDS = frozenset(
    {"latitude", "longitude", "is_frozen", "is_active", "school", "address"}
)
_BUS_RECALC_FIELDS = frozenset({"capacity", "is_in_service", "school"})

_state = threading.local()


def _recalc_suppressed():
    """True si les recalculs automatiques sont suspendus (import en masse)."""
    return getattr(_state, "depth", 0) > 0


@contextmanager
def recalc_hold():
    """Suspend temporairement les recalculs automatiques (imports Excel en
    masse) : le moteur est relancé une seule fois à la fin de l'import,
    sinon chaque ligne déclencherait un recalcul complet."""
    _state.depth = getattr(_state, "depth", 0) + 1
    try:
        yield
    finally:
        _state.depth = getattr(_state, "depth", 0) - 1


def _trigger_recalc(school):
    """Relance le moteur central pour l'école, sans jamais casser l'opération
    d'origine : les erreurs sont journalisées, pas propagées."""
    if school is None or _recalc_suppressed():
        return
    try:
        recalculate_school_routes(school)
    except Exception:
        logger.exception("Recalcul automatique des trajets échoué (école %s).", school)


@receiver(post_save, sender=Student)
def student_post_save(sender, instance, created, **kwargs):
    """Gel/dégel, position GPS du domicile, adresse/profil ou changement
    d'école → recalcul automatique des trajets de l'école."""
    if _recalc_suppressed():
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not update_fields.intersection(
        _STUDENT_RECALC_FIELDS
    ):
        return
    _trigger_recalc(instance.school)


@receiver(post_delete, sender=Student)
def student_post_delete(sender, instance, **kwargs):
    """Suppression d'un élève → les bus concernés sont recalculés."""
    _trigger_recalc(instance.school)


@receiver(post_save, sender=Bus)
def bus_post_save(sender, instance, created, **kwargs):
    """Ajout d'un bus, changement de capacité ou de service → recalcul."""
    if _recalc_suppressed():
        return
    update_fields = kwargs.get("update_fields")
    if update_fields is not None and not update_fields.intersection(
        _BUS_RECALC_FIELDS
    ):
        return
    _trigger_recalc(instance.school)


@receiver(post_delete, sender=Bus)
def bus_post_delete(sender, instance, **kwargs):
    """Suppression d'un bus → ses élèves sont réaffectés aux bus restants."""
    _trigger_recalc(instance.school)
