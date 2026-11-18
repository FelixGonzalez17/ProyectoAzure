from decimal import Decimal
from uuid import UUID, uuid4

from PIL import Image
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.db.models import F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from farmacia.auth_forms import EmployeeAuthenticationForm
from farmacia.models import (
    GRUPO_FARMACEUTICO,
    GRUPO_JEFE_FARMACIA,
    ConsultaJefeFarmacia,
    IngresoInventario,
    Medicamento,
    SimulacionPago,
    DetalleVentaCaja,
    VentaCaja,
)
from farmacia.consulta_forms import ConsultaJefeFarmaciaForm


class IniciarSesionView(LoginView):
    template_name = "farmacia/login.html"
    authentication_form = EmployeeAuthenticationForm
    redirect_authenticated_user = True

    def get_success_url(self):
        if self.request.user.is_staff:
            return reverse("admin:index")
        return reverse("farmacia:inicio")


def _es_personal_farmacia(user):
    return (
        user.is_authenticated
        and (
            user.is_superuser
            or user.groups.filter(
                name__in=(GRUPO_FARMACEUTICO, GRUPO_JEFE_FARMACIA)
            ).exists()
        )
    )


def _es_jefe_farmacia(user):
    return user.is_authenticated and (
        user.is_superuser
        or user.groups.filter(name=GRUPO_JEFE_FARMACIA).exists()
    )


def _medicamentos_con_stock():
    ingresado = (
        IngresoInventario.objects.filter(
            medicamento_id=OuterRef("pk"),
            fecha_vencimiento__gte=timezone.localdate(),
        )
        .values("medicamento_id")
        .annotate(total=Sum("stock_disponible"))
        .values("total")[:1]
    )
    return Medicamento.objects.filter(activo=True).annotate(
        stock_vendible=Coalesce(
            Subquery(ingresado, output_field=IntegerField()),
            Value(0),
        )
    )


@login_required
def inicio_farmacia(request):
    if not _es_personal_farmacia(request.user):
        raise PermissionDenied

    medicamentos = _medicamentos_con_stock()
    disponibles = medicamentos.filter(stock_vendible__gt=0)
    consultas_pendientes = ConsultaJefeFarmacia.objects.filter(
        estado=ConsultaJefeFarmacia.Estado.PENDIENTE
    )
    if not _es_jefe_farmacia(request.user):
        consultas_pendientes = consultas_pendientes.filter(
            solicitante=request.user
        )
    return render(
        request,
        "farmacia/inicio_farmacia.html",
        {
            "medicamentos": medicamentos.order_by("nombre"),
            "total_medicamentos": medicamentos.count(),
            "total_disponibles": disponibles.count(),
            "consultas_pendientes": consultas_pendientes.count(),
            "consultas_jefe_disponible": (
                request.user.is_superuser
                or request.user.groups.filter(
                    name__in=(GRUPO_FARMACEUTICO, GRUPO_JEFE_FARMACIA)
                ).exists()
            ),
        },
    )


@login_required
@require_http_methods(["GET", "POST"])
def consultas_jefe_farmacia(request):
    if not (
        request.user.is_superuser
        or request.user.groups.filter(
            name__in=(GRUPO_FARMACEUTICO, GRUPO_JEFE_FARMACIA)
        ).exists()
    ):
        raise PermissionDenied

    es_jefe = _es_jefe_farmacia(request.user)
    if request.method == "POST":
        if es_jefe:
            consulta = get_object_or_404(
                ConsultaJefeFarmacia,
                pk=request.POST.get("consulta_id"),
            )
            try:
                consulta.responder(
                    request.user,
                    request.POST.get("respuesta", ""),
                )
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
            else:
                messages.success(request, f"La consulta #{consulta.pk} fue respondida.")
            return redirect("farmacia:consultas_jefe")

        if not request.user.groups.filter(name=GRUPO_FARMACEUTICO).exists():
            raise PermissionDenied
        form = ConsultaJefeFarmaciaForm(request.POST, request.FILES)
        if form.is_valid():
            consulta = form.save(commit=False)
            consulta.solicitante = request.user
            consulta.save()
            messages.success(
                request,
                f"Tu consulta #{consulta.pk} fue enviada al Jefe de Farmacia.",
            )
            return redirect("farmacia:consultas_jefe")
    else:
        form = ConsultaJefeFarmaciaForm() if not es_jefe else None

    consultas = ConsultaJefeFarmacia.objects.select_related(
        "solicitante",
        "jefe_farmacia",
    )
    if es_jefe:
        pendientes = consultas.filter(estado=ConsultaJefeFarmacia.Estado.PENDIENTE)
        historial = consultas.filter(estado=ConsultaJefeFarmacia.Estado.RESPONDIDA)
    else:
        pendientes = ConsultaJefeFarmacia.objects.none()
        historial = consultas.filter(solicitante=request.user)

    pendientes = Paginator(pendientes, 25).get_page(
        request.GET.get("pagina_pendientes")
    )
    historial = Paginator(historial, 25).get_page(
        request.GET.get("pagina_historial")
    )
    consultas_pendientes = ConsultaJefeFarmacia.objects.filter(
        estado=ConsultaJefeFarmacia.Estado.PENDIENTE
    ).count()
    return render(
        request,
        "farmacia/consultas_jefe.html",
        {
            "form": form,
            "es_jefe": es_jefe,
            "pendientes": pendientes,
            "historial": historial,
            "consultas_pendientes": consultas_pendientes,
        },
    )


@login_required
def foto_consulta_jefe_farmacia(request, consulta_id):
    consulta = get_object_or_404(ConsultaJefeFarmacia, pk=consulta_id)
    es_jefe = _es_jefe_farmacia(request.user)
    es_solicitante = (
        request.user.groups.filter(name=GRUPO_FARMACEUTICO).exists()
        and consulta.solicitante_id == request.user.pk
    )
    if not (es_jefe or es_solicitante):
        raise PermissionDenied

    try:
        archivo = consulta.foto_receta.open("rb")
    except OSError as error:
        raise Http404 from error
    try:
        with Image.open(archivo) as imagen:
            content_type = Image.MIME.get(imagen.format)
    except (OSError, Image.DecompressionBombError) as error:
        archivo.close()
        raise Http404 from error
    if content_type not in ("image/jpeg", "image/png", "image/webp"):
        archivo.close()
        raise Http404

    archivo.seek(0)
    respuesta = FileResponse(archivo, content_type=content_type)
    respuesta["X-Content-Type-Options"] = "nosniff"
    respuesta["Cache-Control"] = "private, no-store"
    respuesta["Content-Security-Policy"] = "default-src 'none'; sandbox"
    return respuesta


def _obtener_carrito(request):
    carrito = request.session.get("farmacia_caja_carrito", {})
    if not isinstance(carrito, dict):
        carrito = {}
    return carrito


def _datos_carrito(carrito):
    productos = []
    total = Decimal("0.00")
    unidades = 0
    for medicamento_id, cantidad in carrito.items():
        medicamento = Medicamento.objects.filter(
            pk=medicamento_id,
            activo=True,
        ).first()
        if medicamento is None:
            continue

        try:
            cantidad = int(cantidad)
        except (TypeError, ValueError):
            continue
        if cantidad <= 0:
            continue

        subtotal = None
        if medicamento.precio is not None:
            subtotal = medicamento.precio * cantidad
            total += subtotal
        productos.append(
            {
                "medicamento": medicamento,
                "cantidad": cantidad,
                "subtotal": subtotal,
            }
        )
        unidades += cantidad
    return productos, total, unidades


def _registrar_venta_simulada(request, carrito, medio_pago, checkout_key):
    try:
        clave = UUID(checkout_key)
    except (TypeError, ValueError, AttributeError) as error:
        raise ValidationError("La operación de caja no es válida; vuelve a intentarlo.") from error

    venta_existente = VentaCaja.objects.filter(
        idempotency_key=clave,
        cajero=request.user,
    ).first()
    if venta_existente:
        return venta_existente

    if request.session.get("farmacia_caja_checkout_key") != str(clave):
        raise ValidationError("La operación ya no está vigente; revisa la caja e inténtalo otra vez.")

    try:
        cantidades = {
            int(medicamento_id): int(cantidad)
            for medicamento_id, cantidad in carrito.items()
        }
    except (TypeError, ValueError) as error:
        raise ValidationError("El carrito contiene productos o cantidades no válidas.") from error
    if not cantidades or any(cantidad < 1 for cantidad in cantidades.values()):
        raise ValidationError("Agrega al menos un producto con cantidad válida.")

    if medio_pago not in SimulacionPago.MedioPago.values:
        raise ValidationError("Selecciona un medio de pago válido.")

    hoy = timezone.localdate()
    asignaciones = []
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        venta_existente = VentaCaja.objects.filter(
            idempotency_key=clave,
            cajero=request.user,
        ).first()
        if venta_existente:
            return venta_existente

        medicamentos = {
            medicamento.pk: medicamento
            for medicamento in Medicamento.objects.select_for_update()
            .filter(pk__in=cantidades, activo=True)
            .order_by("pk")
        }
        if len(medicamentos) != len(cantidades):
            raise ValidationError("Uno o más productos ya no están activos.")
        if any(medicamento.requiere_receta for medicamento in medicamentos.values()):
            raise ValidationError(
                "La caja no procesa medicamentos con receta hasta completar "
                "el flujo de validación y retención de recetas."
            )
        if any(medicamento.precio is None for medicamento in medicamentos.values()):
            raise ValidationError("Configura el precio de todos los productos antes de vender.")
        total = sum(
            (
                medicamentos[medicamento_id].precio * cantidad
                for medicamento_id, cantidad in cantidades.items()
            ),
            Decimal("0.00"),
        )
        if total <= 0:
            raise ValidationError("El total de la venta debe ser mayor que cero.")

        lotes = list(
            IngresoInventario.objects.select_for_update()
            .filter(
                medicamento_id__in=cantidades,
                fecha_vencimiento__gte=hoy,
                stock_disponible__gt=0,
            )
            .order_by("medicamento_id", "fecha_vencimiento", "pk")
        )
        lotes_por_medicamento = {}
        for lote in lotes:
            lotes_por_medicamento.setdefault(lote.medicamento_id, []).append(lote)

        for medicamento_id, cantidad_requerida in cantidades.items():
            medicamento = medicamentos[medicamento_id]
            restante = cantidad_requerida
            for lote in lotes_por_medicamento.get(medicamento_id, []):
                asignada = min(restante, lote.stock_disponible)
                if asignada:
                    asignaciones.append((lote, asignada, medicamento.precio))
                    restante -= asignada
                if restante == 0:
                    break
            if restante:
                disponible = cantidad_requerida - restante
                raise ValidationError(
                    f"Stock insuficiente para {medicamento.nombre}: "
                    f"solicitaste {cantidad_requerida} y hay {disponible} disponible(s)."
                )

        venta = VentaCaja.objects.create(
            idempotency_key=clave,
            cajero=request.user,
            total=total,
        )
        for lote, cantidad, precio in asignaciones:
            actualizado = IngresoInventario.objects.filter(
                pk=lote.pk,
                stock_disponible__gte=cantidad,
            ).update(stock_disponible=F("stock_disponible") - cantidad)
            if not actualizado:
                raise ValidationError(
                    f"El stock del lote {lote.lote} cambió; vuelve a revisar la caja."
                )
            DetalleVentaCaja.objects.create(
                venta=venta,
                ingreso=lote,
                cantidad=cantidad,
                precio_unitario=precio,
            )

        SimulacionPago.objects.create(
            venta_caja=venta,
            monto=total,
            medio_pago=medio_pago,
            estado=SimulacionPago.Estado.APROBADO,
            referencia_simulada=f"SIM-{venta.pk}",
        )
    return venta


@login_required
@require_http_methods(["GET", "POST"])
def caja(request):
    if not _es_personal_farmacia(request.user):
        raise PermissionDenied

    carrito = _obtener_carrito(request)
    if request.method == "POST":
        accion = request.POST.get("accion")
        if accion == "agregar":
            medicamento = get_object_or_404(
                Medicamento,
                pk=request.POST.get("medicamento_id"),
                activo=True,
            )
            if medicamento.requiere_receta:
                messages.error(
                    request,
                    "Los medicamentos con receta requieren el flujo de validación "
                    "y retención antes de su venta.",
                )
                return redirect("farmacia:caja")
            try:
                cantidad = int(request.POST.get("cantidad", "1"))
            except (TypeError, ValueError):
                cantidad = 0

            if cantidad < 1:
                messages.error(request, "Ingresa una cantidad mayor que cero.")
                return redirect("farmacia:caja")

            medicamentos = _medicamentos_con_stock()
            stock = medicamentos.filter(pk=medicamento.pk).values_list(
                "stock_vendible",
                flat=True,
            ).first() or 0
            try:
                cantidad_actual = int(carrito.get(str(medicamento.pk), 0))
            except (TypeError, ValueError):
                cantidad_actual = 0
            if cantidad_actual + cantidad > stock:
                messages.error(
                    request,
                    f"Stock disponible para {medicamento.nombre}: {stock} unidades.",
                )
                return redirect("farmacia:caja")

            carrito[str(medicamento.pk)] = cantidad_actual + cantidad
            request.session["farmacia_caja_carrito"] = carrito
            messages.success(request, f"{medicamento.nombre} agregado a la caja.")
            return redirect("farmacia:caja")

        if accion == "quitar":
            carrito.pop(request.POST.get("medicamento_id"), None)
            request.session["farmacia_caja_carrito"] = carrito
            messages.info(request, "Producto quitado de la caja.")
            return redirect("farmacia:caja")

        if accion == "vaciar":
            request.session["farmacia_caja_carrito"] = {}
            messages.info(request, "La caja quedó vacía.")
            return redirect("farmacia:caja")

        if accion == "confirmar_venta":
            medio_pago = request.POST.get("medio_pago")
            checkout_key = request.POST.get("checkout_key")
            try:
                venta = _registrar_venta_simulada(
                    request,
                    carrito,
                    medio_pago,
                    checkout_key,
                )
            except ValidationError as error:
                messages.error(request, " ".join(error.messages))
                return redirect("farmacia:caja")

            request.session["farmacia_caja_carrito"] = {}
            request.session["farmacia_caja_checkout_key"] = str(uuid4())
            messages.success(
                request,
                f"Venta #{venta.pk} registrada. Pago simulado aprobado por "
                f"${venta.total:,.0f}.".replace(",", "."),
            )
            return redirect("farmacia:caja")

        messages.error(request, "No se reconoció la acción solicitada.")
        return redirect("farmacia:caja")

    busqueda = request.GET.get("q", "").strip()
    medicamentos = _medicamentos_con_stock().order_by("nombre")
    if busqueda:
        medicamentos = medicamentos.filter(
            Q(nombre__icontains=busqueda)
            | Q(codigo__icontains=busqueda)
        )

    productos_carrito, total_carrito, unidades_carrito = _datos_carrito(carrito)
    checkout_key = request.session.get("farmacia_caja_checkout_key")
    if not checkout_key:
        checkout_key = str(uuid4())
        request.session["farmacia_caja_checkout_key"] = checkout_key
    pagos_recientes = (
        SimulacionPago.objects.filter(venta_caja__isnull=False)
        .select_related("venta_caja", "venta_caja__cajero")
        .order_by("-fecha_simulacion")[:20]
    )
    return render(
        request,
        "farmacia/caja.html",
        {
            "medicamentos": medicamentos,
            "busqueda": busqueda,
            "productos_carrito": productos_carrito,
            "total_carrito": total_carrito,
            "unidades_carrito": unidades_carrito,
            "checkout_key": checkout_key,
            "medios_pago": SimulacionPago.MedioPago.choices,
            "carrito_tiene_precios": all(
                producto["subtotal"] is not None for producto in productos_carrito
            ),
            "pagos_recientes": pagos_recientes,
        },
    )
