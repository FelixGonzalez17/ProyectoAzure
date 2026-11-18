from django.urls import path

from farmacia.views import (
    caja,
    consultas_jefe_farmacia,
    foto_consulta_jefe_farmacia,
    inicio_farmacia,
)

app_name = "farmacia"

urlpatterns = [
    path("", inicio_farmacia, name="inicio"),
    path("caja/", caja, name="caja"),
    path("consultas-jefe/", consultas_jefe_farmacia, name="consultas_jefe"),
    path(
        "consultas-jefe/<int:consulta_id>/receta/",
        foto_consulta_jefe_farmacia,
        name="foto_consulta_jefe",
    ),
]
