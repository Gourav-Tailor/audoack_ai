from django.contrib import admin

from .models import AudioAnalysis, BatchUpload


class AudioAnalysisInline(admin.TabularInline):
    """Allows viewing audio analysis entries directly inside a BatchUpload detail page."""

    model = AudioAnalysis
    extra = 0
    fields = (
        "filename",
        "status",
        "emotional_tone",
        "emotional_intensity",
        "background_noise_present",
        "audio_quality",
        "confidence",
    )
    readonly_fields = fields
    can_delete = False


@admin.register(BatchUpload)
class BatchUploadAdmin(admin.ModelAdmin):
    list_display = ("id", "zip_file", "status", "uploaded_at", "get_analysis_count")
    list_filter = ("status", "uploaded_at")
    search_fields = ("id", "error_message")
    readonly_fields = ("uploaded_at",)
    inlines = [AudioAnalysisInline]

    @admin.display(description="Total Clips")
    def get_analysis_count(self, obj):
        return obj.analyses.count()


@admin.register(AudioAnalysis)
class AudioAnalysisAdmin(admin.ModelAdmin):
    list_display = (
        "filename",
        "batch",
        "status",
        "emotional_tone",
        "emotional_intensity",
        "background_noise_present",
        "background_noise_severity",
        "audio_quality",
        "confidence",
        "created_at",
    )
    list_filter = (
        "status",
        "emotional_tone",
        "emotional_intensity",
        "background_noise_present",
        "background_noise_severity",
        "audio_quality",
        "speaker_overlap_present",
        "long_silence_present",
    )
    search_fields = ("filename", "background_noise_type", "error_details")
    readonly_fields = ("created_at",)

    fieldsets = (
        (
            "File Information",
            {"fields": ("batch", "filename", "audio_file", "status", "error_details")},
        ),
        ("Emotional Analysis", {"fields": ("emotional_tone", "emotional_intensity")}),
        (
            "Background Noise",
            {
                "fields": (
                    "background_noise_present",
                    "background_noise_type",
                    "background_noise_severity",
                )
            },
        ),
        (
            "Audio & Speech Characteristics",
            {
                "fields": (
                    "audio_quality",
                    "speaker_overlap_present",
                    "long_silence_present",
                )
            },
        ),
        ("Model Metrics", {"fields": ("confidence", "created_at")}),
    )
