from __future__ import annotations

import unittest

from tests.support import es_detector

ORDINARY = (
    "no hiciste nada mal", "no has hecho nada mal, sigue asi", "no hiciste nada raro con el commit",
    "eso no importa ahora", "esto no es urgente", "eso no lo sabia nadie", "esto no tiene prisa",
    "eso no cambia el plan", "esto no afecta a produccion", "eso no pasa casi nunca",
    "eso no me preocupa", "esto no necesita review", "eso no depende de nosotros",
    "ojo, que hay una reunion a las tres", "cuidado, el servidor tarda en arrancar",
    "ojo, que mañana es festivo", "cuidado con el cable del monitor",
    "ojo de pez en la foto", "deja de lado el tema del color", "deja de compartir pantalla",
    "deja de estar pendiente del movil un rato", "deja el archivo donde estaba",
    "ya te dije que si", "te dije que si, que me parece bien", "ya te he dicho que vale",
    "te dije hola ayer", "nunca mas he visto algo igual", "jamas he visto un error asi",
    "nunca mas volvere a ese bar", "mal tiempo hoy en Madrid", "mal de altura en el viaje",
    "hazme un resumen de los cambios", "puedes mirar el PR?", "gracias, quedo perfecto",
    "si no has terminado, sigue con el parche", "avisame hasta que no has visto el registro",
    "mañana te cuento lo de la reunion", "el despliegue salio bien", "no se como hacerlo",
    "no hay problema con el horario", "vale, lo reviso luego", "perfecto, continua",
    "esto no gasta memoria?", "eso no es lo que decia el doc, o si?", "esta bien asi",
    "el test esta mal escrito en el doc viejo?",
)
REBUKES = (
    "eso no, usa la otra rama", "esto no, hazlo con la cola", "así no, quiero el diff completo",
    "eso no es lo que pedí", "esto no funciona, vuelve a mirarlo", "eso no hagas push a main",
    "mal, el archivo es otro", "está mal, falta el modulo", "incorrecto, era otra columna",
    "ya te dije que no uses mocks", "te dije que sin comentarios",
    "deja de resumir cada paso", "deja de repetir lo que ya dije", "deja de inventar rutas",
    "nunca mas hagas push sin avisar", "jamás más toques ese archivo",
    "ojo, eso rompe el build", "cuidado, no toques el core", "ojo, te has dejado el test roto",
    "sigue roto, no has arreglado nada", "oye porque no has puesto el nombre del modulo",
    "no hiciste lo que te pedi en el segundo punto", "pero no has entendido el problema",
)


DETECTOR = es_detector()


def _kept(text):
    return DETECTOR.is_correction(text)


class SpanishPrecisionTests(unittest.TestCase):
    def test_ordinary_sentences_are_not_flagged(self):
        self.assertGreaterEqual(len(ORDINARY), 40)
        for text in ORDINARY:
            with self.subTest(text=text):
                self.assertFalse(_kept(text))

    def test_rebukes_are_flagged(self):
        self.assertGreaterEqual(len(REBUKES), 15)
        for text in REBUKES:
            with self.subTest(text=text):
                self.assertTrue(_kept(text))

    def test_terse_rebukes_flagged_and_their_lookalikes_not(self):
        for text in ("te he dicho que no", "ya te dije que no", "para", "para ya",
                     "otra vez lo mismo", "esto está mal"):
            with self.subTest(text=text):
                self.assertTrue(_kept(text))
        for text in ("para mañana", "te dije que no?", "para qué sirve", "otra vez lo intento"):
            with self.subTest(text=text):
                self.assertFalse(_kept(text))

    def test_reproach_needs_a_clause_start(self):
        self.assertTrue(_kept("vale, no has puesto el test"))
        self.assertFalse(_kept("creo que sin duda no has terminado de leerlo"))
        self.assertFalse(_kept("si no has terminado, sigue con el parche"))


if __name__ == "__main__":
    unittest.main()
