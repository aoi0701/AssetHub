from rest_framework import serializers
from apps.departments.models import Category, Department


class DepartmentSerializer(serializers.ModelSerializer):
    """Serializer for Department model."""

    class Meta:
        model = Department
        fields = ['id', 'name', 'code', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']


class CategorySerializer(serializers.ModelSerializer):
    """Serializer for Category model."""

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'created_at']
        read_only_fields = ['id', 'created_at']
