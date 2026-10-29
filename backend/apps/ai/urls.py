from django.urls import path
from apps.ai.views import AiCategorySuggestView

urlpatterns = [
    path('suggest-category/', AiCategorySuggestView.as_view(), name='ai-suggest-category'),
]
