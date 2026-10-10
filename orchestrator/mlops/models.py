from django.db import models


class ModelStage(models.TextChoices):
    AVAILABLE = "available", "Available"
    SHADOW = "shadow", "Shadow"  # scores traffic, detections are only logged
    ACTIVE = "active", "Active"  # detections affect trust scores


class ModelVersion(models.Model):
    """A trained model found in MODELS_DIR (written by the ml-train job)."""

    version = models.CharField(max_length=128, unique=True)
    kind = models.CharField(max_length=32)
    threshold = models.FloatField()
    metrics = models.JSONField(default=dict, blank=True)
    report_md = models.TextField(blank=True)
    trained_at = models.DateTimeField(null=True, blank=True)
    discovered_at = models.DateTimeField(auto_now_add=True)
    stage = models.CharField(
        max_length=16, choices=ModelStage.choices, default=ModelStage.AVAILABLE
    )
    stage_changed_at = models.DateTimeField(null=True, blank=True)
    missing = models.BooleanField(default=False, help_text="artifact no longer on disk")

    class Meta:
        ordering = ["-trained_at", "-id"]

    def __str__(self) -> str:
        return f"{self.version} ({self.stage})"
