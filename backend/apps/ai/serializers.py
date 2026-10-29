from rest_framework import serializers


class AiCategorySuggestRequestSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=200, required=True, allow_blank=False)
    description = serializers.CharField(
        max_length=2000,
        required=False,
        allow_blank=True,
        default='',
    )


class AiCategorySuggestResponseSerializer(serializers.Serializer):
    request_id = serializers.CharField()
    suggested_category_id = serializers.IntegerField()
    suggested_category_name = serializers.CharField()
    explanation = serializers.CharField(allow_blank=True)
    log_saved = serializers.BooleanField()
