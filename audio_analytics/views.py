import csv
import json
import os
import tempfile
import uuid
from urllib.parse import urlencode

from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.files import File
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from .forms import SignUpForm
from .models import AudioAnalysis, BatchUpload
from .tasks import process_batch_upload_task

PAGE_SIZE_OPTIONS = (10, 25, 50, 100)
DEFAULT_PAGE_SIZE = 10

BATCH_DETAIL_SORT_FIELDS = {
    "filename": "filename",
    "tone": "emotional_tone",
    "intensity": "emotional_intensity",
    "noise": "background_noise_present",
    "noise_severity": "background_noise_severity",
    "quality": "audio_quality",
    "overlap": "speaker_overlap_present",
    "silence": "long_silence_present",
    "confidence": "confidence",
}
DEFAULT_BATCH_DETAIL_SORT = "id"


def get_page_size(request):
    try:
        page_size = int(request.GET.get("page_size", DEFAULT_PAGE_SIZE))
    except (TypeError, ValueError):
        return DEFAULT_PAGE_SIZE
    return page_size if page_size in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE


class SignUpView(View):
    def get(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            return redirect("dashboard")
        form = SignUpForm()
        return render(request, "audio_analytics/signup.html", {"form": form})

    def post(self, request, *args, **kwargs):
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            return redirect("dashboard")
        return render(request, "audio_analytics/signup.html", {"form": form})


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "audio_analytics/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser:
            batch_qs = BatchUpload.objects.all()
            analysis_qs = AudioAnalysis.objects.all()
        else:
            batch_qs = BatchUpload.objects.filter(user=self.request.user)
            analysis_qs = AudioAnalysis.objects.filter(batch__user=self.request.user)
        context["recent_batches"] = batch_qs.order_by("-uploaded_at")[:5]
        context["total_analyzed"] = analysis_qs.filter(status=AudioAnalysis.ProcessingStatus.SUCCESS).count()
        context["failed_count"] = analysis_qs.filter(status=AudioAnalysis.ProcessingStatus.FAILED).count()
        return context


class BatchUploadView(LoginRequiredMixin, View):
    def post(self, request, *args, **kwargs):
        zip_file = request.FILES.get("zip_file")
        if not zip_file or not zip_file.name.endswith(".zip"):
            return JsonResponse({"error": "Please upload a valid .zip archive."}, status=400)
        temp_dir = tempfile.gettempdir()
        temp_zip_path = os.path.join(temp_dir, f"upload_{uuid.uuid4().hex}_{zip_file.name}")
        with open(temp_zip_path, "wb+") as destination:
            for chunk in zip_file.chunks():
                destination.write(chunk)
        with open(temp_zip_path, "rb") as saved_zip:
            batch = BatchUpload.objects.create(
                user=request.user,
                zip_file=File(saved_zip, name=zip_file.name),
                status=BatchUpload.Status.PROCESSING,
            )
        process_batch_upload_task.delay(batch.id, temp_zip_path)
        return redirect("batch_detail", pk=batch.pk)


class BatchListView(LoginRequiredMixin, ListView):
    model = BatchUpload
    template_name = "audio_analytics/batch_list.html"
    context_object_name = "batches"
    ordering = ["-uploaded_at"]

    def get_queryset(self):
        qs = super().get_queryset()
        if not self.request.user.is_superuser:
            qs = qs.filter(user=self.request.user)
        return qs.select_related("device").order_by("-uploaded_at", "-id")

    def get_paginate_by(self, queryset):
        return get_page_size(self.request)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["page_size"] = get_page_size(self.request)
        context["page_size_options"] = PAGE_SIZE_OPTIONS
        return context


class BatchDetailView(LoginRequiredMixin, DetailView):
    model = BatchUpload
    template_name = "audio_analytics/batch_detail.html"
    context_object_name = "batch"

    def get_queryset(self):
        qs = super().get_queryset()
        if self.request.user.is_superuser:
            return qs
        return qs.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_size = get_page_size(self.request)
        sort_field = self.request.GET.get("sort", DEFAULT_BATCH_DETAIL_SORT)
        if sort_field not in BATCH_DETAIL_SORT_FIELDS and sort_field != DEFAULT_BATCH_DETAIL_SORT:
            sort_field = DEFAULT_BATCH_DETAIL_SORT
        sort_direction = self.request.GET.get("direction", "desc").lower()
        if sort_direction not in {"asc", "desc"}:
            sort_direction = "desc"
        sort_expression = BATCH_DETAIL_SORT_FIELDS.get(sort_field, sort_field)
        if sort_direction == "desc":
            sort_expression = f"-{sort_expression}"
        if sort_field == "id":
            analyses_qs = self.object.analyses.all().order_by(sort_expression)
        else:
            analyses_qs = self.object.analyses.all().order_by(sort_expression, "-id")
        paginator = Paginator(analyses_qs, page_size)
        analyses_page = paginator.get_page(self.request.GET.get("page", 1))
        analyses = list(analyses_page.object_list)
        for analysis in analyses:
            analysis.audio_playback_url = analysis.audio_file.url if analysis.audio_file else ""
        context["analyses"] = analyses
        context["page_obj"] = analyses_page
        context["paginator"] = paginator
        context["page_size"] = page_size
        context["page_size_options"] = PAGE_SIZE_OPTIONS
        context["sort_field"] = sort_field
        context["sort_direction"] = sort_direction
        sortable_columns = [
            ("filename", "Filename"), ("tone", "Tone"), ("intensity", "Intensity"),
            ("noise", "Noise Present"), ("noise_severity", "Noise Severity"),
            ("quality", "Audio Quality"), ("overlap", "Overlap"),
            ("silence", "Silence"), ("confidence", "Confidence"),
        ]
        column_context = []
        for key, label in sortable_columns:
            active = key == sort_field
            next_direction = "desc" if active and sort_direction == "asc" else "asc"
            query = urlencode({"page": 1, "page_size": page_size, "sort": key, "direction": next_direction})
            column_context.append({"key": key, "label": label, "active": active, "direction": sort_direction if active else None, "url": f"?{query}"})
        context["sortable_columns"] = column_context

        def page_url(page):
            return "?" + urlencode({"page": page, "page_size": page_size, "sort": sort_field, "direction": sort_direction})

        context["pagination_urls"] = {
            "first": page_url(1),
            "previous": page_url(analyses_page.previous_page_number()) if analyses_page.has_previous() else "",
            "next": page_url(analyses_page.next_page_number()) if analyses_page.has_next() else "",
            "last": page_url(paginator.num_pages),
        }
        chart_data = []
        for analysis in analyses_page.object_list:
            chart_data.append({
                "filename": analysis.filename or f"Clip {analysis.pk}",
                "tone": analysis.emotional_tone or "unknown",
                "confidence": float(analysis.confidence or 0),
                "intensity": analysis.emotional_intensity or "-",
                "noise": bool(analysis.background_noise_present),
                "noise_severity": analysis.background_noise_severity or "-",
                "quality": analysis.audio_quality or "-",
                "overlap": bool(analysis.speaker_overlap_present),
                "silence": bool(analysis.long_silence_present),
            })
        context["chart_data"] = chart_data
        context["metrics"] = json.loads(self.object.metrics_json) if getattr(self.object, "metrics_json", None) else None
        return context


class ExportBatchResultsView(LoginRequiredMixin, View):
    def get(self, request, pk, *args, **kwargs):
        if request.user.is_superuser:
            batch = get_object_or_404(BatchUpload, pk=pk)
        else:
            batch = get_object_or_404(BatchUpload, pk=pk, user=request.user)
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="batch_{batch.id}_results.csv"'
        writer = csv.writer(response)
        writer.writerow(["name", "result_json"])
        for analysis in batch.analyses.all():
            if analysis.status == AudioAnalysis.ProcessingStatus.SUCCESS:
                result_payload = json.dumps(analysis.to_dict())
            else:
                result_payload = json.dumps({"error": analysis.error_details})
            writer.writerow([analysis.filename, result_payload])
        return response


class TranscriptsView(LoginRequiredMixin, TemplateView):
    template_name = "audio_analytics/transcripts.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if self.request.user.is_superuser:
            batches = BatchUpload.objects.all()
            analyses = AudioAnalysis.objects.all()
        else:
            batches = BatchUpload.objects.filter(user=self.request.user)
            analyses = AudioAnalysis.objects.filter(batch__user=self.request.user)
        batch_id = self.request.GET.get("batch")
        if batch_id:
            try:
                selected_batch_id = int(batch_id)
                analyses = analyses.filter(batch_id=selected_batch_id)
            except (TypeError, ValueError):
                selected_batch_id = None
        else:
            selected_batch_id = None
        analyses = analyses.exclude(transcript__isnull=True).exclude(transcript="").select_related("batch", "batch__device").order_by("-batch__uploaded_at", "-id")
        paginator = Paginator(analyses, 50)
        page_obj = paginator.get_page(self.request.GET.get("page", 1))
        context["batches"] = batches.order_by("-uploaded_at", "-id")
        context["selected_batch_id"] = selected_batch_id
        context["page_obj"] = page_obj
        context["transcripts"] = page_obj.object_list
        return context


class RecordsView(LoginRequiredMixin, ListView):
    """Human-focused recording history. No batch IDs or technical metrics are exposed."""
    model = BatchUpload
    template_name = "audio_analytics/records.html"
    context_object_name = "records"
    paginate_by = 20

    def get_queryset(self):
        qs = BatchUpload.objects.select_related("user", "device").annotate(
            chunk_count=Count(
                "analyses",
                filter=Q(analyses__status=AudioAnalysis.ProcessingStatus.SUCCESS),
                distinct=True,
            )
        )
        if not self.request.user.is_superuser:
            qs = qs.filter(user=self.request.user)

        tone = self.request.GET.get("tone", "").strip().lower()
        valid_tones = {value for value, _label in AudioAnalysis.EmotionalTone.choices}
        if tone in valid_tones:
            qs = qs.annotate(
                tone_match_count=Count(
                    "analyses",
                    filter=Q(
                        analyses__status=AudioAnalysis.ProcessingStatus.SUCCESS,
                        analyses__emotional_tone=tone,
                    ),
                    distinct=True,
                )
            ).filter(tone_match_count__gt=0).order_by("-tone_match_count", "-uploaded_at", "-id")
        else:
            qs = qs.order_by("-uploaded_at", "-id")
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["selected_tone"] = self.request.GET.get("tone", "").strip().lower()
        context["tone_choices"] = AudioAnalysis.EmotionalTone.choices
        return context


class RecordDetailView(LoginRequiredMixin, DetailView):
    """Shows only the useful human-facing information for one recording."""
    model = BatchUpload
    template_name = "audio_analytics/record_detail.html"
    context_object_name = "record"

    def get_queryset(self):
        qs = BatchUpload.objects.select_related("user", "device")
        if self.request.user.is_superuser:
            return qs
        return qs.filter(user=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        analyses = (
            AudioAnalysis.objects.filter(batch=self.object, status=AudioAnalysis.ProcessingStatus.SUCCESS)
            .exclude(transcript__isnull=True)
            .exclude(transcript="")
            .order_by("id")
        )
        paginator = Paginator(analyses, 20)
        page_obj = paginator.get_page(self.request.GET.get("page", 1))
        chunks = []
        for number, analysis in enumerate(page_obj.object_list, start=page_obj.start_index()):
            chunks.append({
                "number": number,
                "transcript": analysis.transcript,
                "audio_url": analysis.audio_file.url if analysis.audio_file else "",
                "created_at": analysis.created_at,
                "tone": analysis.get_emotional_tone_display() if analysis.emotional_tone else "",
            })
        context["chunks"] = chunks
        context["page_obj"] = page_obj
        context["chunk_count"] = paginator.count
        return context
