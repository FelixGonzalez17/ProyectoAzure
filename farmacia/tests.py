from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from farmacia.models import (
    GRUPO_FARMACEUTICO,
    GRUPO_JEFE_FARMACIA,
    ConsultaJefeFarmacia,
    IngresoInventario,
    Medicamento,
    Merma,
    SimulacionPago,
    VentaCaja,
)

User = get_user_model()


class AccesoYFarmaciaTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="jefe",
            email="jefe@example.com",
            password="Admin-password-123",
        )

    def test_login_es_pagina_inicial_sin_registro_ni_catalogo_publico(self):
        respuesta = self.client.get(reverse("login"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Acceso a tu cuenta")
        self.assertNotContains(respuesta, "Registrarse")
        self.assertNotContains(respuesta, "catalogo")
        self.assertNotContains(respuesta, "autofocus")
        self.assertContains(respuesta, "pie-sitio")
        self.assertContains(respuesta, "Tel&eacute;fono")
        self.assertContains(respuesta, "farmacia/sitio.css?v=3")

    def test_superusuario_jefe_qf_va_al_admin_al_iniciar_sesion(self):
        self.admin.username = "jefe_qf"
        self.admin.save(update_fields=("username",))

        respuesta = self.client.post(
            reverse("login"),
            {
                "username": "jefe_qf",
                "password": "Admin-password-123",
                "next": "/",
            },
        )

        self.assertRedirects(
            respuesta,
            reverse("admin:index"),
            fetch_redirect_response=False,
        )

    def test_usuario_sin_grupo_no_puede_iniciar_sesion(self):
        usuario = User.objects.create_user(
            username="usuario",
            password="Usuario-password-123",
        )

        respuesta = self.client.post(
            reverse("login"),
            {
                "username": "usuario",
                "password": "Usuario-password-123",
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "exclusiva para personal autorizado")
        self.assertFalse("_auth_user_id" in self.client.session)

    def test_usuario_staff_sin_rol_farmaceutico_no_puede_iniciar_sesion(self):
        usuario = User.objects.create_user(
            username="staff_sin_rol",
            password="Staff-password-123",
            is_staff=True,
        )

        respuesta = self.client.post(
            reverse("login"),
            {
                "username": "staff_sin_rol",
                "password": "Staff-password-123",
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "exclusiva para personal autorizado")

    def test_quimico_va_a_su_inicio_de_farmacia_al_iniciar_sesion(self):
        quimico = User.objects.create_user(
            username="quimico",
            password="Quimico-password-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])

        respuesta = self.client.post(
            reverse("login"),
            {
                "username": "quimico",
                "password": "Quimico-password-123",
            },
        )

        self.assertRedirects(
            respuesta,
            reverse("farmacia:inicio"),
            fetch_redirect_response=False,
        )

    def test_quimico_ve_catalogo_con_precio_y_stock_en_su_inicio(self):
        quimico = User.objects.create_user(
            username="quimico_catalogo",
            password="Quimico-password-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-QF-001",
            nombre="Producto de farmacia",
            precio=Decimal("2490.00"),
        )
        IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-QF-001",
            cantidad=12,
            fecha_vencimiento=timezone.localdate() + timedelta(days=90),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)

        respuesta = self.client.get(reverse("farmacia:inicio"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Producto de farmacia")
        self.assertContains(respuesta, "MED-QF-001")
        self.assertContains(respuesta, "$2.490")
        self.assertContains(respuesta, "Sin receta")
        self.assertContains(respuesta, "Existencias: 12")
        self.assertContains(respuesta, "Ir a Caja")
        self.assertContains(respuesta, "pie-sitio")

    def test_jefe_qf_puede_crear_medicamento_con_receta_desde_admin(self):
        self.client.force_login(self.admin)

        formulario = self.client.get(reverse("admin:farmacia_medicamento_add"))
        self.assertEqual(formulario.status_code, 200)
        self.assertContains(formulario, "Requiere receta")

        respuesta = self.client.post(
            reverse("admin:farmacia_medicamento_add"),
            {
                "nombre": "Medicamento con receta",
                "codigo": "MED-RECETA-001",
                "descripcion": "Requiere receta médica.",
                "precio": "3500.00",
                "requiere_receta": "on",
                "stock_minimo": "5",
                "activo": "on",
                "_save": "Guardar",
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        medicamento = Medicamento.objects.get(codigo="MED-RECETA-001")
        self.assertTrue(medicamento.requiere_receta)

    def test_medicamento_con_receta_se_indica_en_catalogo_y_caja(self):
        quimico = User.objects.create_user(
            username="quimico_recetas",
            password="Quimico-password-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-RECETA-002",
            nombre="Producto sujeto a receta",
            requiere_receta=True,
        )
        IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-RECETA-002",
            cantidad=3,
            fecha_vencimiento=timezone.localdate() + timedelta(days=90),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)

        inicio = self.client.get(reverse("farmacia:inicio"))
        caja = self.client.get(reverse("farmacia:caja"))

        self.assertContains(inicio, "Con receta")
        self.assertContains(caja, "Con receta")

    def test_caja_permita_agregar_producto_y_muestre_total(self):
        quimico = User.objects.create_user(
            username="quimico_caja",
            password="Quimico-password-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-CAJA-001",
            nombre="Producto para caja",
            precio=Decimal("1250.00"),
        )
        IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-CAJA-001",
            cantidad=5,
            fecha_vencimiento=timezone.localdate() + timedelta(days=90),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)

        respuesta = self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "agregar",
                "medicamento_id": medicamento.pk,
                "cantidad": "2",
            },
        )

        self.assertRedirects(
            respuesta,
            reverse("farmacia:caja"),
            fetch_redirect_response=False,
        )
        respuesta = self.client.get(reverse("farmacia:caja"))
        self.assertContains(respuesta, "Producto para caja")
        self.assertContains(respuesta, "$2.500")
        self.assertContains(respuesta, "Confirmar venta y descontar stock")
        self.assertContains(respuesta, "pie-sitio")
        self.assertEqual(
            self.client.session["farmacia_caja_carrito"],
            {str(medicamento.pk): 2},
        )

    def test_caja_registra_pago_y_descuenta_lotes_por_vencimiento(self):
        quimico = User.objects.create_user(
            username="quimico_venta",
            password="Venta-test-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-VENTA-001",
            nombre="Medicamento para vender",
            precio=Decimal("1250.00"),
        )
        lote_proximo = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VENTA-PROXIMO",
            cantidad=2,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
            registrado_por=self.admin,
        )
        lote_posterior = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VENTA-POSTERIOR",
            cantidad=5,
            fecha_vencimiento=timezone.localdate() + timedelta(days=60),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)
        self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "agregar",
                "medicamento_id": medicamento.pk,
                "cantidad": "4",
            },
        )
        respuesta = self.client.get(reverse("farmacia:caja"))
        checkout_key = respuesta.context["checkout_key"]

        respuesta = self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "confirmar_venta",
                "checkout_key": checkout_key,
                "medio_pago": SimulacionPago.MedioPago.DEBITO,
            },
        )

        self.assertRedirects(
            respuesta,
            reverse("farmacia:caja"),
            fetch_redirect_response=False,
        )
        venta = VentaCaja.objects.get()
        pago = SimulacionPago.objects.get(venta_caja=venta)
        self.assertEqual(venta.cajero, quimico)
        self.assertEqual(venta.total, Decimal("5000.00"))
        self.assertEqual(pago.estado, SimulacionPago.Estado.APROBADO)
        self.assertEqual(pago.medio_pago, SimulacionPago.MedioPago.DEBITO)
        pagos = self.client.get(reverse("farmacia:caja"))
        self.assertContains(pagos, "Pagos simulados recientes")
        self.assertContains(pagos, "Débito")
        self.assertEqual(
            list(
                venta.detalles.order_by("ingreso__fecha_vencimiento").values_list(
                    "ingreso_id",
                    "cantidad",
                )
            ),
            [(lote_proximo.pk, 2), (lote_posterior.pk, 2)],
        )
        lote_proximo.refresh_from_db()
        lote_posterior.refresh_from_db()
        self.assertEqual(lote_proximo.stock_disponible, 0)
        self.assertEqual(lote_posterior.stock_disponible, 3)

        self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "confirmar_venta",
                "checkout_key": checkout_key,
                "medio_pago": SimulacionPago.MedioPago.DEBITO,
            },
        )
        self.assertEqual(VentaCaja.objects.count(), 1)
        self.assertEqual(SimulacionPago.objects.filter(venta_caja=venta).count(), 1)
        lote_posterior.refresh_from_db()
        self.assertEqual(lote_posterior.stock_disponible, 3)

    def test_caja_rechaza_venta_si_el_carrito_supera_stock_actual(self):
        quimico = User.objects.create_user(
            username="quimico_stock_venta",
            password="Venta-test-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-VENTA-002",
            nombre="Medicamento con stock limitado",
            precio=Decimal("800.00"),
        )
        ingreso = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VENTA-LIMITADO",
            cantidad=1,
            fecha_vencimiento=timezone.localdate() + timedelta(days=60),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)
        respuesta = self.client.get(reverse("farmacia:caja"))
        checkout_key = respuesta.context["checkout_key"]
        session = self.client.session
        session["farmacia_caja_carrito"] = {str(medicamento.pk): 2}
        session.save()

        respuesta = self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "confirmar_venta",
                "checkout_key": checkout_key,
                "medio_pago": SimulacionPago.MedioPago.EFECTIVO,
            },
            follow=True,
        )

        self.assertContains(respuesta, "Stock insuficiente")
        self.assertFalse(VentaCaja.objects.exists())
        self.assertFalse(SimulacionPago.objects.exists())
        ingreso.refresh_from_db()
        self.assertEqual(ingreso.stock_disponible, 1)

    def test_caja_no_vende_medicamento_con_receta_sin_validacion(self):
        quimico = User.objects.create_user(
            username="quimico_receta_caja",
            password="Venta-test-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-VENTA-RECETA",
            nombre="Medicamento con receta",
            precio=Decimal("1200.00"),
            requiere_receta=True,
        )
        IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VENTA-RECETA",
            cantidad=3,
            fecha_vencimiento=timezone.localdate() + timedelta(days=60),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)

        respuesta = self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "agregar",
                "medicamento_id": medicamento.pk,
                "cantidad": "1",
            },
            follow=True,
        )

        self.assertContains(respuesta, "requieren el flujo de validación")
        self.assertEqual(self.client.session.get("farmacia_caja_carrito", {}), {})
        self.assertFalse(VentaCaja.objects.exists())

    def crear_imagen_receta(self):
        archivo = BytesIO()
        Image.new("RGB", (4, 4), color="white").save(archivo, format="PNG")
        return SimpleUploadedFile(
            "receta.png",
            archivo.getvalue(),
            content_type="image/png",
        )

    def crear_quimico(self, username="quimico"):
        quimico = User.objects.create_user(
            username=username,
            password="Quimico-password-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        return quimico

    def test_quimico_envia_consulta_con_foto_y_solo_remitente_o_jefe_ve_la_foto(self):
        quimico = self.crear_quimico()
        self.client.force_login(quimico)

        respuesta = self.client.post(
            reverse("farmacia:consultas_jefe"),
            {
                "asunto": "Duda sobre indicación",
                "pregunta": "¿Podría confirmar la dosis indicada?",
                "foto_receta": self.crear_imagen_receta(),
            },
        )

        self.assertRedirects(
            respuesta,
            reverse("farmacia:consultas_jefe"),
            fetch_redirect_response=False,
        )
        consulta = ConsultaJefeFarmacia.objects.get()
        self.addCleanup(lambda: consulta.foto_receta.delete(save=False))
        self.assertEqual(consulta.solicitante, quimico)
        self.assertEqual(consulta.estado, ConsultaJefeFarmacia.Estado.PENDIENTE)

        foto = self.client.get(
            reverse("farmacia:foto_consulta_jefe", args=(consulta.pk,))
        )
        self.assertEqual(foto.status_code, 200)
        self.assertEqual(foto["Content-Type"], "image/png")
        self.assertEqual(foto["X-Content-Type-Options"], "nosniff")
        self.assertEqual(foto["Cache-Control"], "private, no-store")
        foto.close()

        otro_quimico = self.crear_quimico("otro_quimico")
        self.client.force_login(otro_quimico)
        foto_no_autorizada = self.client.get(
            reverse("farmacia:foto_consulta_jefe", args=(consulta.pk,))
        )
        self.assertEqual(foto_no_autorizada.status_code, 403)

        jefe = User.objects.create_user(
            username="jefe_farmacia",
            is_active=True,
        )
        jefe.groups.add(Group.objects.get_or_create(name=GRUPO_JEFE_FARMACIA)[0])
        self.client.force_login(jefe)
        foto_jefe = self.client.get(
            reverse("farmacia:foto_consulta_jefe", args=(consulta.pk,))
        )
        self.assertEqual(foto_jefe.status_code, 200)
        foto_jefe.close()

    def test_jefe_responde_consulta_solo_una_vez(self):
        quimico = self.crear_quimico()
        consulta = ConsultaJefeFarmacia.objects.create(
            solicitante=quimico,
            asunto="Consulta de prueba",
            pregunta="¿Cuál es la indicación?",
            foto_receta=self.crear_imagen_receta(),
        )
        self.addCleanup(lambda: consulta.foto_receta.delete(save=False))
        jefe = User.objects.create_user(username="jefe_farmacia", is_active=True)
        jefe.groups.add(Group.objects.get_or_create(name=GRUPO_JEFE_FARMACIA)[0])
        self.client.force_login(jefe)

        respuesta = self.client.post(
            reverse("farmacia:consultas_jefe"),
            {"consulta_id": consulta.pk, "respuesta": "  Dosis confirmada.  "},
        )

        self.assertRedirects(
            respuesta,
            reverse("farmacia:consultas_jefe"),
            fetch_redirect_response=False,
        )
        consulta.refresh_from_db()
        self.assertEqual(consulta.estado, ConsultaJefeFarmacia.Estado.RESPONDIDA)
        self.assertEqual(consulta.respuesta, "Dosis confirmada.")
        self.assertEqual(consulta.jefe_farmacia, jefe)
        self.assertIsNotNone(consulta.fecha_respuesta)

        respuesta = self.client.post(
            reverse("farmacia:consultas_jefe"),
            {"consulta_id": consulta.pk, "respuesta": "Otra respuesta."},
            follow=True,
        )
        self.assertContains(respuesta, "ya fue respondida")
        consulta.refresh_from_db()
        self.assertEqual(consulta.respuesta, "Dosis confirmada.")

    def test_formulario_consulta_rechaza_archivo_que_no_es_imagen(self):
        quimico = self.crear_quimico()
        self.client.force_login(quimico)
        respuesta = self.client.post(
            reverse("farmacia:consultas_jefe"),
            {
                "asunto": "Duda sobre receta",
                "pregunta": "Consulta de prueba",
                "foto_receta": SimpleUploadedFile(
                    "receta.png",
                    b"esto no es una imagen",
                    content_type="image/png",
                ),
            },
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.context["form"].errors["foto_receta"])
        self.assertFalse(ConsultaJefeFarmacia.objects.exists())

    def test_usuario_sin_grupo_no_puede_abrir_consultas(self):
        usuario = User.objects.create_user(
            username="usuario_sin_rol",
            password="Usuario-password-123",
            is_active=True,
        )
        self.client.force_login(usuario)

        respuesta = self.client.get(reverse("farmacia:consultas_jefe"))

        self.assertEqual(respuesta.status_code, 403)

    def test_caja_rechaza_cantidad_superior_al_stock_vendible(self):
        quimico = User.objects.create_user(
            username="quimico_stock",
            password="Quimico-password-123",
            is_active=True,
        )
        quimico.groups.add(Group.objects.get_or_create(name=GRUPO_FARMACEUTICO)[0])
        medicamento = Medicamento.objects.create(
            codigo="MED-CAJA-002",
            nombre="Producto con poco stock",
            precio=Decimal("500.00"),
        )
        IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-CAJA-002",
            cantidad=1,
            fecha_vencimiento=timezone.localdate() + timedelta(days=90),
            registrado_por=self.admin,
        )
        self.client.force_login(quimico)

        self.client.post(
            reverse("farmacia:caja"),
            {
                "accion": "agregar",
                "medicamento_id": medicamento.pk,
                "cantidad": "2",
            },
        )

        self.assertEqual(
            self.client.session.get("farmacia_caja_carrito", {}),
            {},
        )

    def test_personal_sin_privilegios_no_puede_ver_solicitudes(self):
        usuario_staff = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="Staff-password-123",
            is_staff=True,
        )
        self.client.force_login(usuario_staff)

        respuesta = self.client.get(
            reverse("admin:farmacia_solicitudcuenta_changelist")
        )

        self.assertEqual(respuesta.status_code, 403)

    def test_panel_admin_muestra_resumen_de_farmacia(self):
        self.client.force_login(self.admin)

        respuesta = self.client.get(reverse("admin:index"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "farmacia/admin.css?v=5")
        self.assertContains(respuesta, "Resumen de inventario")
        self.assertContains(respuesta, "Abrir Caja")
        self.assertContains(respuesta, "Consultas al Jefe de Farmacia")
        self.assertContains(
            respuesta,
            reverse("farmacia:consultas_jefe"),
        )
        self.assertContains(respuesta, reverse("farmacia:caja"))
        self.assertContains(respuesta, "Registrar o revisar ingresos")
        self.assertContains(respuesta, "Solicitudes de qu")
        self.assertNotContains(respuesta, "pie-sitio")

    def test_jefe_no_ve_accesos_restringidos_a_solicitudes_de_cuenta(self):
        jefe = User.objects.create_user(
            username="jefe_farmacia",
            password="Jefe-password-123",
            is_staff=True,
        )
        jefe.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="farmacia",
                codename="view_medicamento",
            )
        )
        self.client.force_login(jefe)

        respuesta = self.client.get(reverse("admin:index"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Control de farmacia")
        self.assertNotContains(
            respuesta,
            reverse("admin:farmacia_solicitudcuenta_changelist"),
        )

    def test_listas_principales_del_admin_abren_sin_error(self):
        self.client.force_login(self.admin)
        nombres = (
            "farmacia_medicamento_changelist",
            "farmacia_ingresoinventario_changelist",
            "farmacia_merma_changelist",
            "farmacia_ventarecetaretenida_changelist",
            "farmacia_simulacionpago_changelist",
            "farmacia_ventacaja_changelist",
            "farmacia_detalleventacaja_changelist",
            "farmacia_autorizacionexcepcional_changelist",
            "farmacia_solicitudcuenta_changelist",
            "auth_user_changelist",
            "auth_group_changelist",
        )

        for nombre in nombres:
            with self.subTest(pantalla=nombre):
                respuesta = self.client.get(reverse(f"admin:{nombre}"))
                self.assertEqual(respuesta.status_code, 200)

    def test_permisos_de_usuario_se_muestran_con_nombres_claros(self):
        tipo_medicamento = ContentType.objects.get_for_model(Medicamento)
        Permission.objects.get_or_create(
            content_type=tipo_medicamento,
            codename="delete_medicamento",
            defaults={"name": "Can delete medicamento"},
        )
        self.client.force_login(self.admin)

        respuesta = self.client.get(
            reverse("admin:auth_user_change", args=(self.admin.pk,))
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Eliminar medicamento")
        self.assertContains(respuesta, "Permisos asignados directamente")
        self.assertNotContains(respuesta, "Can delete medicamento")

    def test_formulario_para_crear_usuario_sigue_disponible(self):
        self.client.force_login(self.admin)

        respuesta = self.client.get(reverse("admin:auth_user_add"))

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Crear cuenta de usuario")

    def test_permisos_de_grupo_se_muestran_con_nombres_claros(self):
        tipo_medicamento = ContentType.objects.get_for_model(Medicamento)
        Permission.objects.get_or_create(
            content_type=tipo_medicamento,
            codename="delete_medicamento",
            defaults={"name": "Can delete medicamento"},
        )
        grupo = Group.objects.create(name="Inventario")
        self.client.force_login(self.admin)

        respuesta = self.client.get(
            reverse("admin:auth_group_change", args=(grupo.pk,))
        )

        self.assertEqual(respuesta.status_code, 200)
        self.assertContains(respuesta, "Eliminar medicamento")
        self.assertContains(respuesta, "Permisos incluidos en este grupo")

    def test_administrador_puede_registrar_ingreso_desde_el_panel(self):
        medicamento = Medicamento.objects.create(
            codigo="MED-ADMIN-001",
            nombre="Medicamento para ingreso",
        )
        self.client.force_login(self.admin)

        respuesta = self.client.post(
            reverse("admin:farmacia_ingresoinventario_add"),
            {
                "medicamento": medicamento.pk,
                "lote": "LOTE-ADMIN-01",
                "cantidad": 20,
                "fecha_vencimiento": (
                    timezone.localdate() + timedelta(days=180)
                ).isoformat(),
                "proveedor": "Proveedor de prueba",
                "_save": "Guardar",
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        ingreso = IngresoInventario.objects.get(lote="LOTE-ADMIN-01")
        self.assertEqual(ingreso.registrado_por, self.admin)

    def test_administrador_puede_registrar_merma_desde_el_panel(self):
        medicamento = Medicamento.objects.create(
            codigo="MED-ADMIN-002",
            nombre="Medicamento para merma",
        )
        ingreso = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-ADMIN-02",
            cantidad=10,
            fecha_vencimiento=timezone.localdate() + timedelta(days=180),
            registrado_por=self.admin,
        )
        self.client.force_login(self.admin)

        respuesta = self.client.post(
            reverse("admin:farmacia_merma_add"),
            {
                "ingreso": ingreso.pk,
                "cantidad": 2,
                "motivo": Merma.Motivo.DANADO,
                "observacion": "Envase dañado al recibirlo",
                "_save": "Guardar",
            },
        )

        self.assertEqual(respuesta.status_code, 302)
        merma = Merma.objects.get(ingreso=ingreso)
        self.assertEqual(merma.registrado_por, self.admin)
        ingreso.refresh_from_db()
        self.assertEqual(ingreso.stock_disponible, 8)

    def test_stock_calcula_ingresos_menos_mermas_y_limita_merma(self):
        medicamento = Medicamento.objects.create(
            codigo="MED-001",
            nombre="Medicamento de prueba",
            stock_minimo=2,
        )
        ingreso = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-001",
            cantidad=10,
            fecha_vencimiento=timezone.localdate() + timedelta(days=180),
            proveedor="Proveedor de prueba",
            registrado_por=self.admin,
        )
        Merma.objects.create(
            ingreso=ingreso,
            cantidad=3,
            motivo=Merma.Motivo.DANADO,
            registrado_por=self.admin,
        )

        self.assertEqual(medicamento.stock_actual, 7)
        self.assertEqual(ingreso.stock_disponible, 7)

        with self.assertRaises(ValidationError):
            Merma.objects.create(
                ingreso=ingreso,
                cantidad=8,
                motivo=Merma.Motivo.PERDIDA,
                registrado_por=self.admin,
            )

        self.assertEqual(ingreso.mermas.count(), 1)
        self.assertEqual(ingreso.mermas.count(), 1)

    def test_ingreso_no_acepta_lote_vencido(self):
        medicamento = Medicamento.objects.create(
            codigo="MED-INGRESO-VENCIDO",
            nombre="Medicamento vencido",
        )
        ingreso = IngresoInventario(
            medicamento=medicamento,
            lote="LOTE-INGRESO-VENCIDO",
            cantidad=1,
            fecha_vencimiento=timezone.localdate() - timedelta(days=1),
            registrado_por=self.admin,
        )

        with self.assertRaises(ValidationError):
            ingreso.save()

        self.assertFalse(
            IngresoInventario.objects.filter(lote="LOTE-INGRESO-VENCIDO").exists()
        )

    def test_lotes_vencidos_no_aparecen_como_stock_disponible_en_admin(self):
        medicamento = Medicamento.objects.create(
            codigo="MED-VENCIDO-001",
            nombre="Producto con lote vencido",
            stock_minimo=2,
        )
        ingreso_vencido = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VENCIDO-01",
            cantidad=10,
            fecha_vencimiento=timezone.localdate() + timedelta(days=1),
            registrado_por=self.admin,
        )
        IngresoInventario.objects.filter(pk=ingreso_vencido.pk).update(
            fecha_vencimiento=timezone.localdate() - timedelta(days=1)
        )
        IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VIGENTE-01",
            cantidad=3,
            fecha_vencimiento=timezone.localdate() + timedelta(days=30),
            registrado_por=self.admin,
        )
        self.client.force_login(self.admin)

        inicio = self.client.get(reverse("admin:index"))
        lista = self.client.get(reverse("admin:farmacia_medicamento_changelist"))

        self.assertEqual(medicamento.stock_actual, 3)
        self.assertEqual(inicio.context["resumen_farmacia"]["stock_bajo"], 0)
        medicamento_en_lista = lista.context["cl"].result_list[0]
        self.assertEqual(medicamento_en_lista._stock_actual, 3)

    def test_solo_lotes_vencidos_cuentan_como_stock_bajo_en_admin(self):
        medicamento = Medicamento.objects.create(
            codigo="MED-VENCIDO-002",
            nombre="Producto vencido sin stock vigente",
            stock_minimo=2,
        )
        ingreso_vencido = IngresoInventario.objects.create(
            medicamento=medicamento,
            lote="LOTE-VENCIDO-02",
            cantidad=10,
            fecha_vencimiento=timezone.localdate() + timedelta(days=1),
            registrado_por=self.admin,
        )
        IngresoInventario.objects.filter(pk=ingreso_vencido.pk).update(
            fecha_vencimiento=timezone.localdate() - timedelta(days=1)
        )
        self.client.force_login(self.admin)

        inicio = self.client.get(reverse("admin:index"))

        self.assertEqual(inicio.context["resumen_farmacia"]["stock_bajo"], 1)
