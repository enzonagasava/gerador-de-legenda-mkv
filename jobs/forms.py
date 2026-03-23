from __future__ import annotations

from django import forms

from core.security import mkv_path_is_allowed

from .models import Job


class JobCreateForm(forms.Form):
    mkv_path = forms.CharField(
        label="Caminho do MKV",
        help_text="Informe o caminho para um arquivo .mkv já existente no servidor.",
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

    def clean_mkv_path(self) -> str:
        value = self.cleaned_data["mkv_path"].strip()
        if not value.lower().endswith(".mkv"):
            raise forms.ValidationError("`mkv_path` precisa apontar para um arquivo `.mkv`.")

        # Usamos validação de existência + segurança contra path traversal.
        import os

        if not os.path.isfile(value):
            raise forms.ValidationError("Arquivo não encontrado: verifique o caminho no servidor.")

        if not mkv_path_is_allowed(value):
            raise forms.ValidationError("`mkv_path` está fora das pastas permitidas (MKV_ALLOWED_ROOTS).")

        # Mantém formato absoluto para consistência.
        return os.path.abspath(value)

