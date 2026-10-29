from django.urls import path
from apps.reports.views import ReportChartsView, ReportKPIView

urlpatterns = [
    path('kpis/', ReportKPIView.as_view(), name='report-kpis'),
    path('charts/', ReportChartsView.as_view(), name='report-charts'),
]
