from django.urls import path

from . import views


urlpatterns = [
    path("chat", views.chat, name="chat"),
    path("chat/history", views.chat_history, name="chat_history"),
    path("learning-goal/<int:learning_goal_id>/memories", views.learning_goal_memories, name="learning_goal_memories"),
    path("learning-goal/<int:learning_goal_id>/memories/proposals", views.learning_goal_memory_proposal, name="learning_goal_memory_proposal"),
    path("learning-goal/<int:learning_goal_id>/memories/<str:memory_id>/confirm", views.learning_goal_memory_confirm, name="learning_goal_memory_confirm"),
    path("learning-goal/<int:learning_goal_id>/memories/<str:memory_id>/revoke", views.learning_goal_memory_revoke, name="learning_goal_memory_revoke"),
    path("learning-goal/<int:learning_goal_id>/conversation", views.learning_goal_conversation_delete, name="learning_goal_conversation_delete"),
]
