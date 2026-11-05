"""URL routing for the Assistant app."""
from django.urls import path

from .views import (
    ConversationListCreateView,
    EscalateTurnView,
    ProcedureDetailView,
    SupportTicketDetailView,
    SupportTicketListView,
    SupportTicketReplyView,
    TurnDetailView,
    TurnListCreateView,
)

urlpatterns = [
    path('conversations/', ConversationListCreateView.as_view(), name='assistant-conversations'),
    path('conversations/<uuid:conversation_id>/turns/', TurnListCreateView.as_view(), name='assistant-conversation-turns'),
    path('turns/<uuid:turn_id>/', TurnDetailView.as_view(), name='assistant-turn-detail'),
    path('turns/<uuid:turn_id>/escalate/', EscalateTurnView.as_view(), name='assistant-turn-escalate'),
    path('procedures/<str:topic>/', ProcedureDetailView.as_view(), name='assistant-procedure-detail'),
    path('support-tickets/', SupportTicketListView.as_view(), name='assistant-tickets'),
    path('support-tickets/<uuid:ticket_id>/', SupportTicketDetailView.as_view(), name='assistant-ticket-detail'),
    path('support-tickets/<uuid:ticket_id>/reply/', SupportTicketReplyView.as_view(), name='assistant-ticket-reply'),
]
