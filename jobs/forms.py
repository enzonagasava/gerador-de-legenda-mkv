from __future__ import annotations

from django import forms

from .models import Job


class JobCreateForm(forms.Form):
    mkv_file = forms.FileField(
        label="Arquivo MKV",
        help_text="Envie um arquivo `.mkv` para processamento.",
    )
    track_number = forms.IntegerField(label="Track (opcional)", required=False, min_value=1)
    idioma_destino = forms.CharField(label="Idioma destino", required=False, max_length=16, initial="pt")
    # Nesta configuração “libretranslate-only”, não oferecemos Google na UI.
    translation_backend = forms.ChoiceField(
        label="Backend de tradução",
        choices=[(Job.TranslationBackend.LIBRETRANSLATE.value, "libretranslate")],
        initial=Job.TranslationBackend.LIBRETRANSLATE.value,
        required=False,
    )

    def clean_mkv_file(self):
        uploaded = self.cleaned_data["mkv_file"]
        name = (uploaded.name or "").lower()
        if not name.endswith(".mkv"):
            raise forms.ValidationError("Envie um arquivo com extensão `.mkv`.")
        return uploaded

