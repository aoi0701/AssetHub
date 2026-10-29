from rest_framework import serializers


class ReportKPISerializer(serializers.Serializer):
    total_active = serializers.IntegerField()
    available = serializers.IntegerField()
    in_use = serializers.IntegerField()
    damaged_under_maintenance = serializers.IntegerField()
    pending_disposal = serializers.IntegerField()
    pending_requests = serializers.IntegerField()
    last_updated = serializers.CharField()


class StatusDistributionItemSerializer(serializers.Serializer):
    status = serializers.CharField()
    label = serializers.CharField()
    count = serializers.IntegerField()


class CategoryDistributionItemSerializer(serializers.Serializer):
    category_id = serializers.IntegerField()
    category_name = serializers.CharField()
    count = serializers.IntegerField()


class DepartmentDistributionItemSerializer(serializers.Serializer):
    department_id = serializers.IntegerField(allow_null=True)
    department_name = serializers.CharField()
    count = serializers.IntegerField()


class MonthlyTrendItemSerializer(serializers.Serializer):
    month = serializers.CharField()
    new_assets = serializers.IntegerField()
    disposed_assets = serializers.IntegerField()


class ReportChartsSerializer(serializers.Serializer):
    status_distribution = StatusDistributionItemSerializer(many=True)
    category_distribution = CategoryDistributionItemSerializer(many=True)
    department_distribution = DepartmentDistributionItemSerializer(many=True)
    monthly_trend = MonthlyTrendItemSerializer(many=True)
